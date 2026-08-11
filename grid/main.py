from contextlib import asynccontextmanager
import asyncio
import json
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from config import (
    API_VERSION,
    COORDINATOR_URL,
    GRID_MODE,
    GRID_VERSION,
    INSTANCE_TTL,
    MAX_BODY_SIZE,
    PORT_END,
    PORT_START,
    PROXY_ADVERTISE_HOST,
    WORKER_API_URL,
    WORKER_ID,
    WORKER_REGISTER_INTERVAL,
    WORKER_STALE_AFTER,
)
from instance_manager import manager
from models import (
    CreateInstanceResponse,
    HealthResponse,
    InstanceDetail,
    InstanceSummary,
    MessageResponse,
    RenewResponse,
    RuleCreate,
    RuleResponse,
    WorkerInfo,
    WorkerRegistrationRequest,
)
from rule_registry import (
    cleanup_all_blobs,
    delete_rule,
    externalize_blobs,
    load_rules,
    save_rules,
    toggle_rule,
)
from worker_registry import RemoteGridClient, RemoteGridError, WorkerRegistry, WorkerState

log = logging.getLogger("grid.api")

DATA_DIR = Path("/data")
CA_DIR = Path("/ca")
DASHBOARD_PATH = Path("/app/dashboard.html")

remote = RemoteGridClient()
workers = WorkerRegistry(stale_after=WORKER_STALE_AFTER)


def _proxy_endpoint(port: int) -> dict:
    return {
        "proxyHost": PROXY_ADVERTISE_HOST,
        "proxyPort": port,
        "proxyUrl": f"http://{PROXY_ADVERTISE_HOST}:{port}",
        "workerId": WORKER_ID if GRID_MODE == "worker" else None,
    }


def _decorate_local_instance(info: dict) -> dict:
    result = dict(info)
    result.update(_proxy_endpoint(info["port"]))
    return result


def _load_client_ips(instance_id: str) -> list[str]:
    clients_file = DATA_DIR / f"{instance_id}_clients.json"
    if not clients_file.exists():
        return []
    try:
        data = json.loads(clients_file.read_text())
        return [c["ip"] for c in data]
    except (json.JSONDecodeError, IOError, KeyError, TypeError):
        return []


def _local_health() -> dict:
    return {
        "status": "UP",
        "instances": len(manager.instances),
        "usedPorts": sorted(manager.used_ports),
        "availableSlots": manager.available_slots,
        "portRange": f"{PORT_START}-{PORT_END}",
        "defaultTtl": INSTANCE_TTL,
        "gridVersion": GRID_VERSION,
        "apiVersion": API_VERSION,
        "mode": GRID_MODE,
        "workerId": WORKER_ID if GRID_MODE == "worker" else None,
        "proxyHost": PROXY_ADVERTISE_HOST,
        "workers": [],
    }


def _worker_registration_payload() -> dict:
    health = _local_health()
    return {
        "workerId": WORKER_ID,
        "apiUrl": WORKER_API_URL,
        "proxyHost": PROXY_ADVERTISE_HOST,
        "availableSlots": health["availableSlots"],
        "instances": health["instances"],
        "gridVersion": GRID_VERSION,
        "apiVersion": API_VERSION,
    }


def _remote_json(worker: WorkerState, method: str, path: str, body=None):
    try:
        return remote.request_json(worker.api_url, method, path, body)
    except RemoteGridError as exc:
        raise HTTPException(exc.status_code, detail=exc.detail) from exc
    except RuntimeError as exc:
        raise HTTPException(503, detail=str(exc)) from exc


def _remote_text(worker: WorkerState, method: str, path: str) -> str:
    try:
        return remote.request_text(worker.api_url, method, path)
    except RemoteGridError as exc:
        raise HTTPException(exc.status_code, detail=exc.detail) from exc
    except RuntimeError as exc:
        raise HTTPException(503, detail=str(exc)) from exc


def _fetch_worker_instances(worker: WorkerState) -> list[dict]:
    response = remote.request_json(worker.api_url, "GET", "instances")
    return response or []


def _owner_or_404(instance_id: str) -> WorkerState:
    owner = workers.discover_owner(instance_id, _fetch_worker_instances)
    if owner is None:
        raise HTTPException(404, detail="Instance not found")
    return owner


def _coordinator_instances() -> list[dict]:
    result: list[dict] = []
    for worker in workers.workers():
        try:
            instances = _fetch_worker_instances(worker)
        except Exception as exc:
            log.warning("Unable to list instances from worker %s: %s", worker.worker_id, exc)
            continue
        for info in instances:
            info.setdefault("workerId", worker.worker_id)
            info.setdefault("proxyHost", worker.proxy_host)
            info.setdefault("proxyPort", info.get("port"))
            if info.get("proxyHost") and info.get("proxyPort"):
                info.setdefault("proxyUrl", f"http://{info['proxyHost']}:{info['proxyPort']}")
            instance_id = info.get("instanceId")
            if instance_id:
                workers.remember_instance(instance_id, worker.worker_id, adjust_capacity=False)
            result.append(info)
    return result


async def _reaper_loop():
    while True:
        await asyncio.sleep(10)
        try:
            reaped = await asyncio.to_thread(manager.reap_expired)
            if reaped:
                log.info("Reaper destroyed %d expired instance(s)", len(reaped))
        except Exception:
            log.exception("Reaper error")


async def _worker_registration_loop():
    if not COORDINATOR_URL:
        log.warning("GRID_MODE=worker without COORDINATOR_URL; worker will not be discoverable")
        return
    while True:
        try:
            payload = _worker_registration_payload()
            await asyncio.to_thread(
                remote.request_json,
                COORDINATOR_URL,
                "POST",
                "workers/register",
                payload,
            )
        except Exception as exc:
            log.warning("Worker registration failed: %s", exc)
        await asyncio.sleep(WORKER_REGISTER_INTERVAL)


async def _registry_reaper_loop():
    while True:
        await asyncio.sleep(max(2.0, WORKER_REGISTER_INTERVAL))
        stale = workers.prune()
        if stale:
            log.warning("Removed stale worker(s): %s", ", ".join(stale))


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []
    if GRID_MODE != "coordinator":
        tasks.append(asyncio.create_task(_reaper_loop()))
    if GRID_MODE == "worker":
        tasks.append(asyncio.create_task(_worker_registration_loop()))
    if GRID_MODE == "coordinator":
        tasks.append(asyncio.create_task(_registry_reaper_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


DESCRIPTION = f"""
## mitmproxy Grid API

Manage isolated mitmproxy instances through a single API.

Grid version: **{GRID_VERSION}**  
API version: **{API_VERSION}**  
Mode: **{GRID_MODE}**

In coordinator mode, workers register their capacity and the coordinator routes every
instance operation to the worker that owns it. Workers advertise the proxy endpoint
that browser nodes should use, so Selenium Grid and mitmproxy-grid can scale independently.
"""

app = FastAPI(
    title="mitmproxy Grid",
    description=DESCRIPTION,
    version=GRID_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
def dashboard():
    return HTMLResponse(content=DASHBOARD_PATH.read_text())


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health():
    if GRID_MODE != "coordinator":
        return _local_health()

    active_workers = workers.workers()
    worker_info = [worker.to_dict() for worker in active_workers]
    return {
        "status": "UP" if active_workers else "DEGRADED",
        "instances": sum(worker.instances for worker in active_workers),
        "usedPorts": [],
        "availableSlots": sum(worker.available_slots for worker in active_workers),
        "portRange": "distributed",
        "defaultTtl": INSTANCE_TTL,
        "gridVersion": GRID_VERSION,
        "apiVersion": API_VERSION,
        "mode": GRID_MODE,
        "workers": worker_info,
    }


@app.post("/workers/register", response_model=MessageResponse, tags=["Workers"])
def register_worker(registration: WorkerRegistrationRequest):
    if GRID_MODE != "coordinator":
        raise HTTPException(404, detail="Worker registration is only available in coordinator mode")
    if registration.apiVersion != API_VERSION:
        raise HTTPException(
            409,
            detail=f"Worker API version {registration.apiVersion} is incompatible with coordinator API version {API_VERSION}",
        )
    workers.register(registration.model_dump())
    return {"status": "registered", "message": f"Worker {registration.workerId} registered"}


@app.get("/workers", response_model=List[WorkerInfo], tags=["Workers"])
def list_workers():
    if GRID_MODE != "coordinator":
        raise HTTPException(404, detail="Worker listing is only available in coordinator mode")
    return [worker.to_dict() for worker in workers.workers()]


@app.get("/instances", response_model=List[InstanceSummary], tags=["Instances"])
def list_instances():
    if GRID_MODE == "coordinator":
        return _coordinator_instances()

    result = []
    for info in manager.list_instances():
        iid = info["instanceId"]
        rule_file = DATA_DIR / f"{iid}.json"
        decorated = _decorate_local_instance(info)
        decorated["ruleCount"] = len(load_rules(rule_file))
        decorated["clientIps"] = _load_client_ips(iid)
        result.append(decorated)
    return result


@app.post("/instances", response_model=CreateInstanceResponse, status_code=201, tags=["Instances"])
def create_instance(
    ttl: Optional[int] = Query(None, description="Instance lifespan in seconds. Defaults to INSTANCE_TTL env var."),
):
    if GRID_MODE == "coordinator":
        worker = workers.select_worker()
        if worker is None:
            raise HTTPException(503, detail="No proxy worker has available capacity")
        path = f"instances?ttl={ttl}" if ttl is not None else "instances"
        response = _remote_json(worker, "POST", path)
        workers.remember_instance(response["instanceId"], worker.worker_id)
        return response

    try:
        instance_id, port, effective_ttl, expires_at = manager.create(ttl)
    except RuntimeError as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    return {
        "instanceId": instance_id,
        "port": port,
        **_proxy_endpoint(port),
        "status": "running",
        "ttl": effective_ttl,
        "expiresAt": expires_at,
    }


@app.get("/instances/{instance_id}", response_model=InstanceDetail, tags=["Instances"])
def get_instance(instance_id: str):
    if GRID_MODE == "coordinator":
        worker = _owner_or_404(instance_id)
        return _remote_json(worker, "GET", f"instances/{instance_id}")

    instance = manager.get(instance_id)
    if not instance:
        raise HTTPException(404, detail="Instance not found")
    rule_file = DATA_DIR / f"{instance_id}.json"
    rules_data = load_rules(rule_file)
    info = _decorate_local_instance(instance.to_dict())
    info["rules"] = [{"index": i, **rule} for i, rule in enumerate(rules_data)]
    info["clientIps"] = _load_client_ips(instance_id)
    return info


@app.delete("/instances/{instance_id}", response_model=MessageResponse, tags=["Instances"])
def delete_instance(
    instance_id: str,
    cleanup: bool = Query(False, description="Also remove rule files and CA directory"),
):
    if GRID_MODE == "coordinator":
        worker = _owner_or_404(instance_id)
        path = f"instances/{instance_id}?cleanup=true" if cleanup else f"instances/{instance_id}"
        response = _remote_json(worker, "DELETE", path)
        workers.forget_instance(instance_id)
        return response

    rule_file = DATA_DIR / f"{instance_id}.json"
    cleanup_all_blobs(rule_file)
    if not manager.destroy(instance_id, cleanup=cleanup):
        raise HTTPException(404, detail="Instance not found")
    return {"status": "destroyed", "message": f"Instance {instance_id} terminated"}


@app.post("/instances/{instance_id}/renew", response_model=RenewResponse, tags=["Instances"])
def renew_instance(
    instance_id: str,
    ttl: Optional[int] = Query(None, description="New TTL in seconds. If omitted the instance's current TTL is reused."),
):
    if GRID_MODE == "coordinator":
        worker = _owner_or_404(instance_id)
        path = f"instances/{instance_id}/renew?ttl={ttl}" if ttl is not None else f"instances/{instance_id}/renew"
        return _remote_json(worker, "POST", path)

    instance = manager.renew(instance_id, ttl)
    if not instance:
        raise HTTPException(404, detail="Instance not found")
    return {
        "status": "renewed",
        "message": f"Instance lifespan extended by {instance.ttl}s",
        "ttl": instance.ttl,
        "expiresAt": instance.expires_at.isoformat(),
        "remainingSeconds": round(instance.remaining_seconds, 1),
    }


@app.get("/instances/{instance_id}/rules", response_model=List[RuleResponse], tags=["Rules"])
def list_rules(instance_id: str):
    if GRID_MODE == "coordinator":
        worker = _owner_or_404(instance_id)
        return _remote_json(worker, "GET", f"instances/{instance_id}/rules")

    if not manager.get(instance_id):
        raise HTTPException(404, detail="Instance not found")
    rule_file = DATA_DIR / f"{instance_id}.json"
    rules = load_rules(rule_file)
    return [{"index": i, **rule} for i, rule in enumerate(rules)]


@app.post("/instances/{instance_id}/rules", response_model=MessageResponse, status_code=201, tags=["Rules"])
def create_rule(instance_id: str, rule: RuleCreate):
    if GRID_MODE == "coordinator":
        worker = _owner_or_404(instance_id)
        return _remote_json(worker, "POST", f"instances/{instance_id}/rules", rule.model_dump(exclude_none=True))

    if not manager.get(instance_id):
        raise HTTPException(404, detail="Instance not found")

    rule_dict = rule.model_dump(exclude_none=True)
    for key in ("modifyRequest", "modifyResponse"):
        mod = rule_dict.get("action", {}).get(key)
        if mod:
            mod.pop("bodyFile", None)
            mod.pop("bodyBase64Size", None)

    for key in ("modifyRequest", "modifyResponse"):
        mod = rule_dict.get("action", {}).get(key)
        if mod and mod.get("bodyBase64"):
            estimated_size = len(mod["bodyBase64"]) * 3 // 4
            if estimated_size > MAX_BODY_SIZE:
                raise HTTPException(
                    413,
                    detail=(
                        f"bodyBase64 decoded size (~{estimated_size} bytes) exceeds maximum allowed "
                        f"({MAX_BODY_SIZE} bytes / {MAX_BODY_SIZE // 1048576} MB). Configure MAX_BODY_SIZE env var to increase the limit."
                    ),
                )

    externalize_blobs(rule_dict, instance_id)
    rule_file = DATA_DIR / f"{instance_id}.json"
    rules_data = load_rules(rule_file)
    rules_data.append(rule_dict)
    save_rules(rule_file, rules_data)
    return {"status": "created", "message": f"Rule added (total: {len(rules_data)})"}


@app.delete("/instances/{instance_id}/rules/{rule_index}", response_model=MessageResponse, tags=["Rules"])
def remove_rule(instance_id: str, rule_index: int):
    if GRID_MODE == "coordinator":
        worker = _owner_or_404(instance_id)
        return _remote_json(worker, "DELETE", f"instances/{instance_id}/rules/{rule_index}")

    if not manager.get(instance_id):
        raise HTTPException(404, detail="Instance not found")
    rule_file = DATA_DIR / f"{instance_id}.json"
    if not delete_rule(rule_file, rule_index):
        raise HTTPException(404, detail="Rule index out of range")
    return {"status": "deleted", "message": f"Rule {rule_index} removed"}


@app.patch("/instances/{instance_id}/rules/{rule_index}/toggle", response_model=MessageResponse, tags=["Rules"])
def toggle_rule_endpoint(instance_id: str, rule_index: int):
    if GRID_MODE == "coordinator":
        worker = _owner_or_404(instance_id)
        return _remote_json(worker, "PATCH", f"instances/{instance_id}/rules/{rule_index}/toggle")

    if not manager.get(instance_id):
        raise HTTPException(404, detail="Instance not found")
    rule_file = DATA_DIR / f"{instance_id}.json"
    new_state = toggle_rule(rule_file, rule_index)
    if new_state is None:
        raise HTTPException(404, detail="Rule index out of range")
    state_label = "enabled" if new_state else "disabled"
    return {"status": "toggled", "message": f"Rule {rule_index} is now {state_label}"}


@app.get("/instances/{instance_id}/cert", tags=["Certificates"])
def get_cert(instance_id: str):
    if GRID_MODE == "coordinator":
        worker = _owner_or_404(instance_id)
        return Response(
            content=_remote_text(worker, "GET", f"instances/{instance_id}/cert"),
            media_type="application/x-pem-file",
        )

    if not manager.get(instance_id):
        raise HTTPException(404, detail="Instance not found")
    cert = CA_DIR / instance_id / "mitmproxy-ca-cert.pem"
    if not cert.exists():
        raise HTTPException(404, detail="Certificate not ready yet — try again shortly")
    return Response(content=cert.read_text(), media_type="application/x-pem-file")
