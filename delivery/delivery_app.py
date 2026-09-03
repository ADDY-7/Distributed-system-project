"""
Delivery service.

This is the WebRTC "answerer": whenever a Store Node wants to hand off an
order, it POSTs a WebRTC SDP offer to /webrtc/offer here. We answer it,
a peer-to-peer DataChannel opens between the Store Node process and this
process, and the order arrives as a message on that channel -- no shared
database, no message broker, just a direct peer connection.

Also exposes a plain HTTP endpoint (/deliveries) so the delivery_dashboard.html
page can poll and show what's arrived, and lets a rider mark a delivery done.
"""
import os
import sys
import time
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from shared.webrtc_utils import handle_incoming_offer  # noqa: E402

app = FastAPI(title="Delivery Service")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

DELIVERIES: list[dict] = []


class SDPOffer(BaseModel):
    sdp: str
    type: str


@app.post("/webrtc/offer")
async def webrtc_offer(offer: SDPOffer):
    """A Store Node calls this to open a WebRTC DataChannel to us (signalling over HTTP)."""

    def on_order_received(data: dict):
        data["delivery_record_id"] = str(uuid.uuid4())
        data["received_at"] = time.time()
        data["status"] = "awaiting_pickup"
        DELIVERIES.append(data)
        print(f"[delivery] Received order {data.get('order_id')} from {data.get('store_id')} via WebRTC")

    answer = await handle_incoming_offer(offer.sdp, offer.type, on_order_received)
    return answer


@app.get("/deliveries")
async def list_deliveries():
    """Polled by delivery_dashboard.html to show what's come in over WebRTC."""
    return list(reversed(DELIVERIES))


@app.post("/deliveries/{delivery_record_id}/complete")
async def complete_delivery(delivery_record_id: str):
    for d in DELIVERIES:
        if d.get("delivery_record_id") == delivery_record_id:
            d["status"] = "delivered"
            d["delivered_at"] = time.time()
            return {"ok": True}
    return {"ok": False, "error": "not found"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8005))
    uvicorn.run(app, host="0.0.0.0", port=port)
