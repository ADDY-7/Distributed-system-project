"""
Launches every service in the system with one command:

    python run_all.py

Starts:
  - Middleware        -> http://localhost:8000
  - Store Node A/B/C/D -> http://localhost:8001-8004
  - Delivery service  -> http://localhost:8005
  - Frontend (static HTML) -> http://localhost:8080

Open http://localhost:8080/customer.html to place an order and watch it
flow through the system.
"""
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable

processes = []


def start(name, cmd, env_extra=None, cwd=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    print(f"Starting {name} ...")
    p = subprocess.Popen(cmd, cwd=cwd or ROOT, env=env)
    processes.append((name, p))
    return p


def main():
    # Middleware
    start("middleware", [PYTHON, os.path.join(ROOT, "middleware", "main.py")],
          env_extra={"PORT": "8000"})

    # Delivery (start before stores so it's ready to answer WebRTC offers)
    start("delivery", [PYTHON, os.path.join(ROOT, "delivery", "delivery_app.py")],
          env_extra={"PORT": "8005"})
    time.sleep(1)

    # Store nodes A-D
    store_ports = {"A": 8001, "B": 8002, "C": 8003, "D": 8004}
    for store_id, port in store_ports.items():
        start(f"store-{store_id}", [PYTHON, os.path.join(ROOT, "store_node", "store_app.py")],
              env_extra={"STORE_ID": store_id, "PORT": str(port), "DELIVERY_URL": "http://127.0.0.1:8005"})

    # Frontend static file server
    start("frontend", [PYTHON, "-m", "http.server", "8080"],
          cwd=os.path.join(ROOT, "frontend"))

    print("\nAll services started.")
    print("  Customer UI : http://localhost:8080/customer.html")
    print("  Store A dash: http://localhost:8080/store_dashboard.html?store=A&port=8001")
    print("  Store B dash: http://localhost:8080/store_dashboard.html?store=B&port=8002")
    print("  Store C dash: http://localhost:8080/store_dashboard.html?store=C&port=8003")
    print("  Store D dash: http://localhost:8080/store_dashboard.html?store=D&port=8004")
    print("  Delivery dash: http://localhost:8080/delivery_dashboard.html")
    print("\nPress Ctrl+C to stop everything.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nShutting down...")
        for name, p in processes:
            p.send_signal(signal.SIGTERM)
        for name, p in processes:
            p.wait()
        print("All stopped.")


if __name__ == "__main__":
    main()
