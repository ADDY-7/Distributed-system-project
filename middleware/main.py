"""
Middleware — sits between the Customer UI and the Store Nodes.

For every incoming order it:
  1. Fans out GET /status to every Store Node (A/B/C/D) over HTTP/RPC,
     in parallel, to see current inventory + workload.
  2. Filters out stores that don't have enough stock or are at capacity.
  3. Scores the remaining stores and picks the best one (lowest load %).
  4. Forwards (RPC) the order to that store's POST /order.

The Store Nodes are otherwise unaware of each other -- the Middleware is the
only thing that talks to all of them, which is what lets them behave as
independent distributed nodes.
"""
import asyncio
import os
import sys
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from shared.models import OrderAssignment, OrderRequest, OrderResponse, StoreStatus  # noqa: E402

STORE_URLS = {
    "A": os.environ.get("STORE_A_URL", "http://127.0.0.1:8001"),
    "B": os.environ.get("STORE_B_URL", "http://127.0.0.1:8002"),
    "C": os.environ.get("STORE_C_URL", "http://127.0.0.1:8003"),
    "D": os.environ.get("STORE_D_URL", "http://127.0.0.1:8004"),
}

app = FastAPI(title="Middleware - Smart Order Allocation")
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


async def _fetch_store_status(store_id: str, url: str) -> StoreStatus | None:
    try:
        resp = await http_client.get(f"{url}/status", timeout=3)
        resp.raise_for_status()
        return StoreStatus(**resp.json())
    except Exception:
        return None  # store node unreachable -> treated as offline


@app.get("/stores/status")
async def all_store_status():
    """Handy endpoint for the store dashboards / debugging: current view of every node."""
    results = await asyncio.gather(
        *[_fetch_store_status(sid, url) for sid, url in STORE_URLS.items()]
    )
    return {sid: (s.model_dump() if s else {"store_id": sid, "online": False})
            for sid, s in zip(STORE_URLS.keys(), results)}


def pick_best_store(item: str, qty: int, statuses: dict[str, StoreStatus]) -> str | None:
    """
    Core allocation algorithm:
      - eligible = has enough stock AND has spare capacity
      - among eligible stores, pick lowest workload RATIO (workload / max_capacity)
        so a small store and a big store are compared fairly
      - tie-break alphabetically (A before B before C before D)
    """
    eligible = []
    for store_id, s in statuses.items():
        if s is None:
            continue
        if s.inventory.get(item, 0) < qty:
            continue
        if s.workload + qty > s.max_capacity:
            continue
        load_ratio = s.workload / s.max_capacity if s.max_capacity else 1.0
        eligible.append((load_ratio, store_id))

    if not eligible:
        return None
    eligible.sort(key=lambda t: (t[0], t[1]))
    return eligible[0][1]


@app.post("/order", response_model=OrderResponse)
async def place_order(order: OrderRequest):
    order_id = str(uuid.uuid4())[:8]

    # Step 1: check inventory + workload of every store node (HTTP/RPC, parallel)
    results = await asyncio.gather(
        *[_fetch_store_status(sid, url) for sid, url in STORE_URLS.items()]
    )
    statuses = dict(zip(STORE_URLS.keys(), results))

    # Step 2 + 3: pick the best store
    best_store = pick_best_store(order.item, order.qty, statuses)
    if best_store is None:
        raise HTTPException(
            status_code=409,
            detail=f"No store currently has {order.qty}x '{order.item}' in stock with spare capacity.",
        )

    # Step 4: forward the order to that store (RPC)
    assignment = OrderAssignment(
        order_id=order_id,
        customer_name=order.customer_name,
        item=order.item,
        qty=order.qty,
        address=order.address,
    )
    try:
        resp = await http_client.post(
            f"{STORE_URLS[best_store]}/order", json=assignment.model_dump(), timeout=5
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Store {best_store} rejected order: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach store {best_store}: {e}")

    workload_snapshot = {sid: (s.workload if s else -1) for sid, s in statuses.items()}

    return OrderResponse(
        order_id=order_id,
        status="accepted",
        assigned_store=best_store,
        message=f"Order routed to Store {best_store} (lowest load with stock available).",
        store_workload_snapshot=workload_snapshot,
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
