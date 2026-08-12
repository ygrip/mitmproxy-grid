import os
import socket

GRID_VERSION = os.environ.get("GRID_VERSION", "dev")
API_VERSION = "2"
GRID_MODE = os.environ.get("GRID_MODE", "standalone").strip().lower()
if GRID_MODE not in {"standalone", "worker", "coordinator"}:
    raise RuntimeError("GRID_MODE must be one of: standalone, worker, coordinator")

PORT_START = int(os.environ.get("PORT_START", "10000"))
PORT_END = int(os.environ.get("PORT_END", "10100"))
if PORT_END < PORT_START:
    raise RuntimeError("PORT_END must be greater than or equal to PORT_START")

INSTANCE_TTL = int(os.environ.get("INSTANCE_TTL", "1800"))
MAX_BODY_SIZE = int(os.environ.get("MAX_BODY_SIZE", "52428800"))

WORKER_ID = os.environ.get("WORKER_ID", socket.gethostname())
COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "").rstrip("/")
WORKER_REGISTER_INTERVAL = float(os.environ.get("WORKER_REGISTER_INTERVAL", "5"))
WORKER_STALE_AFTER = float(os.environ.get("WORKER_STALE_AFTER", "20"))


def _container_address() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return socket.gethostname()


_default_proxy_host = "127.0.0.1" if GRID_MODE == "standalone" else _container_address()
PROXY_ADVERTISE_HOST = os.environ.get("PROXY_ADVERTISE_HOST", _default_proxy_host)
WORKER_API_URL = os.environ.get(
    "WORKER_API_URL",
    f"http://{_container_address()}:8090",
).rstrip("/")
