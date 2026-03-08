from contextlib import asynccontextmanager
import asyncio
import base64
import logging
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pathlib import Path
from typing import List, Optional

import json

from instance_manager import manager, PORT_START, PORT_END, INSTANCE_TTL
from rule_registry import (
    load_rules, save_rules, delete_rule, toggle_rule,
    externalize_blobs, cleanup_all_blobs,
)

MAX_BODY_SIZE = int(os.environ.get("MAX_BODY_SIZE", "52428800"))  # 50 MB
from models import (
    RuleCreate,
    RuleResponse,
    InstanceSummary,
    InstanceDetail,
    HealthResponse,
    CreateInstanceResponse,
    RenewResponse,
    MessageResponse,
)

log = logging.getLogger("grid.api")

DATA_DIR = Path("/data")
CA_DIR = Path("/ca")
DASHBOARD_PATH = Path("/app/dashboard.html")


# ── Lifespan (background reaper) ────────────────────────────────────────────


async def _reaper_loop():
    while True:
        await asyncio.sleep(10)
        try:
            reaped = await asyncio.to_thread(manager.reap_expired)
            if reaped:
                log.info("Reaper destroyed %d expired instance(s)", len(reaped))
        except Exception:
            log.exception("Reaper error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_reaper_loop())
    yield
    task.cancel()


# ── App ──────────────────────────────────────────────────────────────────────


DESCRIPTION = """
## mitmproxy Grid API

Manage a fleet of containerised mitmproxy instances, each running on its own
port with independent interception rules and CA certificates.

### Instance lifecycle
Every instance has a configurable **TTL** (default `{ttl}s` via the
`INSTANCE_TTL` environment variable).  Expired instances are automatically
destroyed.  Use **POST /instances/{{id}}/renew** to extend an instance.

### Rule model (v2)
Rules separate **match criteria** from **actions**.
A single rule can modify both the request and the response:

```json
{{
  "match": {{ "urlPattern": ".*api.*", "method": "GET" }},
  "action": {{
    "modifyRequest":  {{ "headers": {{ "set": {{ "X-Debug": "1" }} }} }},
    "modifyResponse": {{ "body": "{{\\"mocked\\": true}}" }}
  }}
}}
```

### Quick start
1. `POST /instances` → spin up a proxy
2. `POST /instances/{{id}}/rules` → add interception rules
3. `GET  /instances/{{id}}/cert` → download CA cert
4. Point your client at `host:{{port}}` as its HTTP proxy

**OpenAPI contract**: [openapi.json](/openapi.json)
""".replace(
    "{ttl}", str(INSTANCE_TTL)
)

app = FastAPI(
    title="mitmproxy Grid",
    description=DESCRIPTION,
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


def _load_client_ips(instance_id: str) -> list[str]:
    clients_file = DATA_DIR / f"{instance_id}_clients.json"
    if not clients_file.exists():
        return []
    try:
        data = json.loads(clients_file.read_text())
        return [c["ip"] for c in data]
    except (json.JSONDecodeError, IOError, KeyError, TypeError):
        return []


# ── Dashboard ────────────────────────────────────────────────────────────────


@app.get("/", include_in_schema=False)
def dashboard():
    return HTMLResponse(content=DASHBOARD_PATH.read_text())


# ── Health ───────────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health():
    """Current health and capacity of the grid."""
    return {
        "status": "UP",
        "instances": len(manager.instances),
        "usedPorts": sorted(manager.used_ports),
        "availableSlots": manager.available_slots,
        "portRange": f"{PORT_START}-{PORT_END}",
        "defaultTtl": INSTANCE_TTL,
    }


# ── Instances ────────────────────────────────────────────────────────────────


@app.get("/instances", response_model=List[InstanceSummary], tags=["Instances"])
def list_instances():
    """List every active mitmproxy instance with summary info."""
    result = []
    for info in manager.list_instances():
        iid = info["instanceId"]
        rule_file = DATA_DIR / f"{iid}.json"
        info["ruleCount"] = len(load_rules(rule_file))
        info["clientIps"] = _load_client_ips(iid)
        result.append(info)
    return result


@app.post(
    "/instances",
    response_model=CreateInstanceResponse,
    status_code=201,
    tags=["Instances"],
)
def create_instance(
    ttl: Optional[int] = Query(
        None,
        description="Instance lifespan in seconds.  Defaults to INSTANCE_TTL env var.",
    ),
):
    """Spin up a new mitmproxy instance on the next available port."""
    try:
        instance_id, port, effective_ttl, expires_at = manager.create(ttl)
    except RuntimeError as exc:
        raise HTTPException(503, detail=str(exc))
    return {
        "instanceId": instance_id,
        "port": port,
        "status": "running",
        "ttl": effective_ttl,
        "expiresAt": expires_at,
    }


@app.get(
    "/instances/{instance_id}",
    response_model=InstanceDetail,
    tags=["Instances"],
)
def get_instance(instance_id: str):
    """Detailed information about a specific instance including its rules."""
    instance = manager.get(instance_id)
    if not instance:
        raise HTTPException(404, detail="Instance not found")

    rule_file = DATA_DIR / f"{instance_id}.json"
    rules_data = load_rules(rule_file)
    rules = [{"index": i, **r} for i, r in enumerate(rules_data)]

    info = instance.to_dict()
    info["rules"] = rules
    info["clientIps"] = _load_client_ips(instance_id)
    return info


@app.delete(
    "/instances/{instance_id}",
    response_model=MessageResponse,
    tags=["Instances"],
)
def delete_instance(
    instance_id: str,
    cleanup: bool = Query(
        False, description="Also remove rule files and CA directory"
    ),
):
    """Terminate a running instance and free its port."""
    rule_file = DATA_DIR / f"{instance_id}.json"
    cleanup_all_blobs(rule_file)
    if not manager.destroy(instance_id, cleanup=cleanup):
        raise HTTPException(404, detail="Instance not found")
    return {"status": "destroyed", "message": f"Instance {instance_id} terminated"}


@app.post(
    "/instances/{instance_id}/renew",
    response_model=RenewResponse,
    tags=["Instances"],
)
def renew_instance(
    instance_id: str,
    ttl: Optional[int] = Query(
        None,
        description="New TTL in seconds.  If omitted the instance's current TTL is reused.",
    ),
):
    """Extend an instance's lifespan.  Resets the expiry clock."""
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


# ── Rules ────────────────────────────────────────────────────────────────────


@app.get(
    "/instances/{instance_id}/rules",
    response_model=List[RuleResponse],
    tags=["Rules"],
)
def list_rules(instance_id: str):
    """List all interception rules for an instance."""
    if not manager.get(instance_id):
        raise HTTPException(404, detail="Instance not found")
    rule_file = DATA_DIR / f"{instance_id}.json"
    rules = load_rules(rule_file)
    return [{"index": i, **r} for i, r in enumerate(rules)]


@app.post(
    "/instances/{instance_id}/rules",
    response_model=MessageResponse,
    status_code=201,
    tags=["Rules"],
)
def create_rule(instance_id: str, rule: RuleCreate):
    """Add a new interception rule.  Takes effect immediately."""
    if not manager.get(instance_id):
        raise HTTPException(404, detail="Instance not found")

    rule_dict = rule.model_dump(exclude_none=True)

    # Security: strip any client-supplied bodyFile (server-managed only)
    for key in ("modifyRequest", "modifyResponse"):
        mod = rule_dict.get("action", {}).get(key)
        if mod:
            mod.pop("bodyFile", None)
            mod.pop("bodyBase64Size", None)

    # Validate bodyBase64 size before externalization
    for key in ("modifyRequest", "modifyResponse"):
        mod = rule_dict.get("action", {}).get(key)
        if mod and mod.get("bodyBase64"):
            estimated_size = len(mod["bodyBase64"]) * 3 // 4
            if estimated_size > MAX_BODY_SIZE:
                raise HTTPException(
                    413,
                    detail=f"bodyBase64 decoded size (~{estimated_size} bytes) exceeds "
                    f"maximum allowed ({MAX_BODY_SIZE} bytes / "
                    f"{MAX_BODY_SIZE // 1048576} MB). "
                    f"Configure MAX_BODY_SIZE env var to increase the limit.",
                )

    externalize_blobs(rule_dict, instance_id)

    rule_file = DATA_DIR / f"{instance_id}.json"
    rules = load_rules(rule_file)
    rules.append(rule_dict)
    save_rules(rule_file, rules)
    return {"status": "created", "message": f"Rule added (total: {len(rules)})"}


@app.delete(
    "/instances/{instance_id}/rules/{rule_index}",
    response_model=MessageResponse,
    tags=["Rules"],
)
def remove_rule(instance_id: str, rule_index: int):
    """Remove a rule by its positional index."""
    if not manager.get(instance_id):
        raise HTTPException(404, detail="Instance not found")
    rule_file = DATA_DIR / f"{instance_id}.json"
    if not delete_rule(rule_file, rule_index):
        raise HTTPException(404, detail="Rule index out of range")
    return {"status": "deleted", "message": f"Rule {rule_index} removed"}


@app.patch(
    "/instances/{instance_id}/rules/{rule_index}/toggle",
    response_model=MessageResponse,
    tags=["Rules"],
)
def toggle_rule_endpoint(instance_id: str, rule_index: int):
    """Toggle a rule between enabled and disabled."""
    if not manager.get(instance_id):
        raise HTTPException(404, detail="Instance not found")
    rule_file = DATA_DIR / f"{instance_id}.json"
    new_state = toggle_rule(rule_file, rule_index)
    if new_state is None:
        raise HTTPException(404, detail="Rule index out of range")
    state_label = "enabled" if new_state else "disabled"
    return {
        "status": "toggled",
        "message": f"Rule {rule_index} is now {state_label}",
    }


# ── Certificates ─────────────────────────────────────────────────────────────


@app.get("/instances/{instance_id}/cert", tags=["Certificates"])
def get_cert(instance_id: str):
    """Download the CA certificate PEM for client configuration."""
    if not manager.get(instance_id):
        raise HTTPException(404, detail="Instance not found")
    cert = CA_DIR / instance_id / "mitmproxy-ca-cert.pem"
    if not cert.exists():
        raise HTTPException(404, detail="Certificate not ready yet — try again shortly")
    return HTMLResponse(
        content=cert.read_text(), media_type="application/x-pem-file"
    )
