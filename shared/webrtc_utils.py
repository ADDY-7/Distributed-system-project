"""
Shared WebRTC helpers built on aiortc (pure-Python WebRTC).

Store Node  = the "offerer": opens an RTCDataChannel and pushes the order.
Delivery    = the "answerer": accepts the offer and listens on the channel.

Signalling (the SDP offer/answer exchange) is done the simplest way possible:
a plain HTTP POST/response, since that's already how every service in this
project talks to every other service (HTTP/RPC). No separate signalling
server is needed for a two-party handshake like this.
"""
import asyncio
import json
from aiortc import RTCPeerConnection, RTCSessionDescription

# Keep references to live peer connections so they aren't garbage collected
# mid-handshake. Cleared out once a handoff finishes.
ACTIVE_PEER_CONNECTIONS = set()


async def wait_for_ice_gathering_complete(pc: RTCPeerConnection):
    """aiortc doesn't do trickle-ICE by default, so we wait until all local
    ICE candidates are gathered before shipping the SDP off to the other side."""
    if pc.iceGatheringState == "complete":
        return
    done = asyncio.get_event_loop().create_future()

    @pc.on("icegatheringstatechange")
    def _on_change():
        if pc.iceGatheringState == "complete" and not done.done():
            done.set_result(None)

    await asyncio.wait_for(done, timeout=10)


async def send_order_over_datachannel(delivery_offer_url: str, payload: dict, http_client) -> dict:
    """
    Runs on the STORE NODE.
    Opens a WebRTC DataChannel straight to the Delivery service and sends
    `payload` (a DeliveryHandoff, as a dict) once the channel is open.
    Returns a small result dict describing what happened.
    """
    pc = RTCPeerConnection()
    ACTIVE_PEER_CONNECTIONS.add(pc)
    channel = pc.createDataChannel("orders")
    channel_open = asyncio.get_event_loop().create_future()

    @channel.on("open")
    def _on_open():
        if not channel_open.done():
            channel_open.set_result(None)

    try:
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await wait_for_ice_gathering_complete(pc)

        # --- signalling over plain HTTP (RPC), same as everything else here ---
        resp = await http_client.post(
            delivery_offer_url,
            json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
            timeout=10,
        )
        resp.raise_for_status()
        answer = resp.json()
        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )

        await asyncio.wait_for(channel_open, timeout=10)
        channel.send(json.dumps(payload))
        # give aiortc a moment to actually flush the message on the wire
        await asyncio.sleep(0.3)
        return {"delivered_via": "webrtc_datachannel", "ok": True}
    except Exception as exc:  # noqa: BLE001 - surface any handoff failure to caller
        return {"delivered_via": "webrtc_datachannel", "ok": False, "error": str(exc)}
    finally:
        asyncio.get_event_loop().call_later(5, lambda: asyncio.ensure_future(_close_pc(pc)))


async def _close_pc(pc: RTCPeerConnection):
    ACTIVE_PEER_CONNECTIONS.discard(pc)
    await pc.close()


async def handle_incoming_offer(sdp: str, type_: str, on_message) -> dict:
    """
    Runs on the DELIVERY service.
    Answers an incoming WebRTC offer from a Store Node and wires up
    `on_message(dict)` to be called for every order that arrives on the
    DataChannel. Returns the SDP answer to send back over HTTP.
    """
    pc = RTCPeerConnection()
    ACTIVE_PEER_CONNECTIONS.add(pc)

    @pc.on("datachannel")
    def _on_datachannel(channel):
        @channel.on("message")
        def _on_message(message):
            try:
                data = json.loads(message)
            except (TypeError, json.JSONDecodeError):
                data = {"raw": message}
            on_message(data)

    offer = RTCSessionDescription(sdp=sdp, type=type_)
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    await wait_for_ice_gathering_complete(pc)

    asyncio.get_event_loop().call_later(15, lambda: asyncio.ensure_future(_close_pc(pc)))
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
