"""
Store Node — an independent distributed node (Store A / B / C / D).

Each store:
  - keeps its OWN inventory and workload in memory (no shared DB — that's
    what makes these independent distributed nodes rather than one service).
  - exposes GET /status so the Middleware can query it over HTTP/RPC.
  - exposes POST /order so the Middleware can hand it an assigned order.
  - once an order is accepted, "packs" it (simulated delay) and then hands
    it off to the Delivery service over a WebRTC DataChannel (aiortc).

Run four of these (STORE_ID=A/B/C/D, different PORTs) to get four
independent nodes. See run_all.py at the project root.
"""
import asyncio
import json
import os
import random
import sys
import time
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from shared.models import OrderAssignment, StoreStatus  # noqa: E402
from shared.webrtc_utils import send_order_over_datachannel  # noqa: E402

# ---------------------------------------------------------------------------
# Per-store defaults. Override with env vars when launching (see run_all.py).
# ---------------------------------------------------------------------------
DEFAULT_CONFIGS = {
    "A": {"name": "Store A - Koregaon Park",   "inventory": {"milk": 40, "bread": 20, "eggs": 60, "rice": 15}, "max_capacity": 10},
    "B": {"name": "Store B - Baner",           "inventory": {"milk": 10, "bread": 35, "eggs": 20, "rice": 40}, "max_capacity": 10},
    "C": {"name": "Store C - Hinjewadi",       "inventory": {"milk": 25, "bread": 5,  "eggs": 45, "rice": 25}, "max_capacity": 10},
    "D": {"name": "Store D - Viman Nagar",     "inventory": {"milk": 5,  "bread": 50, "eggs": 10, "rice": 10}, "max_capacity": 10},
}

STORE_ID = os.environ.get("STORE_ID", "A")
CONFIG = DEFAULT_CONFIGS.get(STORE_ID, DEFAULT_CONFIGS["A"])
STORE_NAME = os.environ.get("STORE_NAME", CONFIG["name"])
INVENTORY = dict(json.loads(os.environ.get("INVENTORY_JSON", json.dumps(CONFIG["inventory"]))))
MAX_CAPACITY = int(os.environ.get("MAX_CAPACITY", CONFIG["max_capacity"]))
DELIVERY_URL = os.environ.get("DELIVERY_URL", "http://127.0.0.1:8005")

state = {
    "inventory": INVENTORY,
    "workload": 0,
    "orders_handled": 0,
}

app = FastAPI(title=f"Store Node {STORE_ID}")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

http_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def startup():
    global http_client
    http_client = httpx.AsyncClient()


@app.on_event("shutdown")
async def shutdown():
    if http_client:
        await http_client.aclose()


@app.get("/status", response_model=StoreStatus)
async def status():
    """Called by the Middleware (HTTP/RPC) to check inventory + current workload."""
    return StoreStatus(
        store_id=STORE_ID,
        store_name=STORE_NAME,
        inventory=state["inventory"],
        workload=state["workload"],
        max_capacity=MAX_CAPACITY,
        online=True,
    )


@app.post("/order")
async def receive_order(order: OrderAssignment):
    """Called by the Middleware once THIS store has been chosen as the best store."""
    have = state["inventory"].get(order.item, 0)
    if have < order.qty:
        raise HTTPException(status_code=409, detail=f"{STORE_ID} out of stock for {order.item}")
    if state["workload"] + order.qty > MAX_CAPACITY:
        raise HTTPException(status_code=503, detail=f"{STORE_ID} at capacity")

    # Reserve stock & bump workload immediately -> this is what makes the
    # store's load change dynamically for the NEXT allocation decision.
    state["inventory"][order.item] -= order.qty
    state["workload"] += order.qty

    asyncio.create_task(_pack_and_handoff(order))

    return {
        "order_id": order.order_id,
        "store_id": STORE_ID,
        "status": "accepted",
        "message": f"{STORE_NAME} accepted order {order.order_id}",
    }


async def _pack_and_handoff(order: OrderAssignment):
    """Simulate picking/packing time, then hand the order to Delivery over WebRTC."""
    pack_seconds = round(random.uniform(1.5, 3.5), 1)
    await asyncio.sleep(pack_seconds)

    handoff_payload = {
        "order_id": order.order_id,
        "store_id": STORE_ID,
        "store_name": STORE_NAME,
        "customer_name": order.customer_name,
        "item": order.item,
        "qty": order.qty,
        "address": order.address,
        "packed_at": time.time(),
    }

    result = await send_order_over_datachannel(
        delivery_offer_url=f"{DELIVERY_URL}/webrtc/offer",
        payload=handoff_payload,
        http_client=http_client,
    )
    if not result.get("ok"):
        print(f"[{STORE_ID}] WebRTC handoff to delivery FAILED: {result.get('error')}")
    else:
        print(f"[{STORE_ID}] Order {order.order_id} handed to delivery via WebRTC DataChannel")

    # Workload clears once the order has physically left the store.
    state["workload"] = max(0, state["workload"] - order.qty)
    state["orders_handled"] += 1


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
