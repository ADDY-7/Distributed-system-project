# Distributed Smart Order Allocation System

A quick-commerce backend where a customer places an order, a middleware picks
the best store (A/B/C/D) based on live inventory + workload, the store node
packs it independently, and hands it off to delivery over a **WebRTC
DataChannel** — all in **Python + HTML**, no other languages.

```
Customer UI  --HTTP-->  Middleware  --HTTP/RPC-->  Store Nodes (A/B/C/D)  --WebRTC DataChannel-->  Delivery
  (HTML)                 (FastAPI)                     (FastAPI, one                                (FastAPI)
                                                       process each, own                             (HTML)
                                                      inventory+workload)
```

## Why this matches the brief, piece by piece

| Brief requirement | How it's implemented |
|---|---|
| Customer places an order | `frontend/customer.html` — plain HTML + `fetch()`, no JS framework |
| Middleware checks inventory, store workload | `middleware/main.py` calls `GET /status` on all 4 store nodes in parallel over HTTP |
| Best store among A/B/C/D auto-selected | `pick_best_store()` in `middleware/main.py` — filters by stock+capacity, ranks by workload ratio |
| Stores work as independent distributed nodes | `store_node/store_app.py` — one process per store, **own in-memory inventory & workload**, no shared DB, no knowledge of each other |
| Store workload changes dynamically | Workload increments the instant an order is accepted, decrements once it's packed & handed to delivery — visible live on the store dashboard and used on every future allocation |
| Store → Delivery uses WebRTC DataChannel | `shared/webrtc_utils.py`, built on **aiortc** (pure-Python WebRTC). Store node opens a peer connection + DataChannel straight to the Delivery process; SDP offer/answer is exchanged over a plain HTTP POST (still "just RPC", no separate signalling server) |
| Python, FastAPI, HTTP/RPC, WebRTC | Every backend service is FastAPI; store↔middleware and offer/answer exchange are HTTP/RPC; store→delivery data itself moves over the WebRTC channel |
| Python + HTML only | Backends: Python (FastAPI + aiortc). Frontend: plain HTML/CSS/vanilla JS `fetch()` calls — no React/Node/anything else |

## Project layout

```
smart-order-allocation/
├── middleware/main.py          Middleware: allocation + RPC to stores
├── store_node/store_app.py     One store node (run 4x as A/B/C/D)
├── delivery/delivery_app.py    Delivery service (WebRTC answerer)
├── shared/
│   ├── models.py                Pydantic contracts shared by every service
│   └── webrtc_utils.py          aiortc offer/answer + DataChannel helpers
├── frontend/
│   ├── customer.html            Place an order
│   ├── store_dashboard.html     Live inventory + workload gauge per store
│   ├── delivery_dashboard.html  Orders arriving over WebRTC
│   └── style.css
├── run_all.py                   Starts everything with one command
└── requirements.txt
```

## Running it

```bash
pip install -r requirements.txt
python run_all.py
```

This starts:
- Middleware — `http://localhost:8000`
- Store Nodes A/B/C/D — `http://localhost:8001` .. `8004`
- Delivery service — `http://localhost:8005`
- Frontend (static HTML server) — `http://localhost:8080`

Then open:
- **`http://localhost:8080/customer.html`** — place an order
- **`http://localhost:8080/store_dashboard.html?store=A&port=8001`** — watch that store's inventory/workload change live (swap `store`/`port` for B/C/D)
- **`http://localhost:8080/delivery_dashboard.html`** — watch orders arrive via WebRTC as each store finishes packing (~1.5–3.5s simulated pack time)

You can also drive it with `curl` to see the raw JSON:

```bash
curl -X POST http://localhost:8000/order \
  -H "Content-Type: application/json" \
  -d '{"customer_name":"Aarav","item":"milk","qty":2,"address":"12 MG Road, Pune"}'
```

## How allocation works

For each order, the middleware:
1. Calls `GET /status` on all 4 stores concurrently (HTTP/RPC).
2. Drops any store that lacks stock or is at capacity.
3. Among the rest, picks the one with the **lowest workload ÷ max_capacity**
   ratio, so a small store and a big store are compared fairly.
4. Forwards the order to that store via `POST /order` (RPC).

This is intentionally a clear, tweakable function — swap in whatever scoring
you want (distance, delivery ETA, priority customers, etc.) in
`pick_best_store()` in `middleware/main.py`.

## How the WebRTC handoff works

- The store node is the WebRTC **offerer**: it opens an `RTCPeerConnection`,
  creates a DataChannel, and POSTs its SDP offer to the delivery service's
  `/webrtc/offer` endpoint.
- The delivery service is the **answerer**: it accepts the offer, replies
  with an SDP answer in the same HTTP response, and listens on the channel
  that opens.
- Once the channel is open, the store sends the order as a JSON message
  directly over the peer-to-peer channel — the delivery service never reads
  it from a database or queue, it receives it as a live message.

This all runs on plain localhost with no STUN/TURN server, since both
processes are on the same machine; if you split store nodes and delivery
across different machines/networks you'd add a STUN server for NAT
traversal (a one-line addition to `RTCPeerConnection(configuration=...)`
in `shared/webrtc_utils.py`).

## Notes / things you can extend

- Inventory and workload are in-memory per store node — restart a store and
  it resets. Swap in SQLite/Postgres per store if you want persistence
  without breaking the "independent node" property.
- `run_all.py` just launches local processes; each store node is written to
  be a fully standalone FastAPI app, so this maps cleanly onto separate
  containers/machines later — just point `DELIVERY_URL` and the middleware's
  `STORE_*_URL` env vars at real hosts.
