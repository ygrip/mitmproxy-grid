# mitmproxy Grid

A self-hosted control plane for isolated [mitmproxy](https://mitmproxy.org/) instances. It can run as a single container or as a coordinator with horizontally scalable proxy workers, which makes it suitable for Selenium Grid and other concurrent automation environments.

## Features

- **Isolated proxy instances** — each active test can own an independent `mitmdump` process, rule set, CA, and proxy port.
- **Horizontal worker scaling** — run a lightweight coordinator plus any number of workers. Every worker may reuse the same internal proxy port range because workers have separate container or pod addresses.
- **Worker discovery** — workers heartbeat their capacity to the coordinator. Stale workers are removed automatically.
- **Coordinator recovery** — if the coordinator loses its in-memory instance-owner map, it can rediscover ownership from registered workers.
- **Advertised proxy endpoints** — instance responses include `proxyHost`, `proxyPort`, and `proxyUrl`, so browser nodes can connect directly to the worker that owns an instance.
- **Automatic TTL and expiry** — abandoned proxy instances are reaped automatically.
- **Request and response interception** — modify headers, query parameters, bodies, status codes, and binary responses with declarative rules.
- **Per-instance CA certificates** — download the CA for HTTPS interception through the REST API.
- **GHCR distribution** — multi-architecture `linux/amd64` and `linux/arm64` images are published only from version tags.

## Supported image

The release introduced by this version is pinned as:

```text
ghcr.io/ygrip/mitmproxy-grid:2.1.0
```

There is intentionally no moving `latest` release contract. Consumers should pin the grid version they support.

## Standalone installation

Use standalone mode when one grid process has enough capacity or when the browser and grid share the same host/network addressing model.

```bash
docker run -d \
  --name mitmproxy-grid \
  --restart unless-stopped \
  -p 8090:8090 \
  -p 10000-10100:10000-10100 \
  -e GRID_MODE=standalone \
  -e INSTANCE_TTL=1800 \
  -v mitmproxy-grid-data:/data \
  -v mitmproxy-grid-ca:/ca \
  ghcr.io/ygrip/mitmproxy-grid:2.1.0
```

Verify it:

```bash
curl http://localhost:8090/health
```

The dashboard is at `http://localhost:8090`, Swagger UI at `/docs`, and ReDoc at `/redoc`.

### Standalone Docker Compose

```bash
git clone https://github.com/ygrip/mitmproxy-grid.git
cd mitmproxy-grid
docker compose up -d
```

`docker-compose.yml` defaults to `2.1.0`. Override only when you deliberately support another release:

```bash
MITMPROXY_GRID_VERSION=2.1.0 docker compose up -d
```

## Scalable deployment beside Selenium Grid

For concurrent Selenium automation, run one coordinator and scale proxy workers on the **same Docker network as the Selenium Grid nodes**.

The topology is:

```text
Jenkins / Testara
       |
       | REST control
       v
mitmproxy-grid coordinator
       |
       +----------+----------+
       |          |          |
   worker-1   worker-2   worker-N
   :10000...  :10000...  :10000...
       ^          ^          ^
       +----------+----------+
                  |
          Selenium Grid nodes
```

Workers do not publish proxy ports to the Jenkins host. Selenium nodes connect directly to the worker address returned in `proxyUrl`, so every worker can reuse the same internal range such as `10000-10009`.

### 1. Use the Selenium Grid Docker network

Assume the existing Selenium Grid network is named `selenium-grid`. If yours has another name, set `AUTOMATION_NETWORK`.

```bash
export AUTOMATION_NETWORK=selenium-grid
```

### 2. Start the coordinator and workers

```bash
docker compose -f docker-compose.scaled.yml up -d --scale mitm-worker=4
```

Or:

```bash
make start-scaled WORKERS=4
```

The default worker range is ten proxy slots per worker:

```text
PORT_START=10000
PORT_END=10009
```

Four workers therefore provide up to forty isolated proxy instances while using the same ten internal port numbers on every worker.

### 3. Check distributed capacity

```bash
curl http://localhost:8090/health
curl http://localhost:8090/workers
```

Coordinator health includes aggregate worker capacity and worker heartbeat information.

### 4. Create a proxy instance

```bash
curl -s -X POST http://localhost:8090/instances | python3 -m json.tool
```

A distributed instance response includes both the legacy `port` field and the routable endpoint:

```json
{
  "instanceId": "f7c1...",
  "port": 10003,
  "proxyHost": "172.20.0.8",
  "proxyPort": 10003,
  "proxyUrl": "http://172.20.0.8:10003",
  "workerId": "mitm-worker-3",
  "status": "running",
  "ttl": 1800,
  "expiresAt": "2026-08-11T10:00:00+00:00"
}
```

**Distributed clients must use `proxyUrl` or `proxyHost` + `proxyPort`.** The coordinator API host is not the proxy host. The legacy `port` field remains for backwards compatibility with standalone consumers.

## Runtime modes

The same image supports three modes:

| `GRID_MODE` | Purpose |
|---|---|
| `standalone` | API and local mitmproxy instances in one container. Default. |
| `coordinator` | Routes instance operations across registered workers. Does not start `mitmdump`. |
| `worker` | Starts local mitmproxy instances and registers capacity with a coordinator. |

Important environment variables:

| Variable | Default | Description |
|---|---:|---|
| `GRID_MODE` | `standalone` | Runtime mode. |
| `PORT_START` | `10000` | First local proxy port owned by a standalone grid or worker. |
| `PORT_END` | `10100` | Last local proxy port. Scaled Compose defaults workers to `10009`. |
| `INSTANCE_TTL` | `1800` | Default instance lifespan in seconds. |
| `MAX_BODY_SIZE` | `52428800` | Maximum decoded `bodyBase64` payload size. |
| `COORDINATOR_URL` | empty | Coordinator REST URL used by workers. |
| `PROXY_ADVERTISE_HOST` | auto | Host/IP returned to clients for worker proxy traffic. Override when automatic container/pod addressing is not routable by browser nodes. |
| `WORKER_API_URL` | auto | Worker REST address registered with the coordinator. |
| `WORKER_REGISTER_INTERVAL` | `5` | Worker heartbeat interval in seconds. |
| `WORKER_STALE_AFTER` | `20` | Time before the coordinator removes a silent worker. |

## Scaling behavior

A worker owns a small local pool of `mitmdump` processes. Scale **worker replicas**, rather than creating one enormous process pool:

```bash
docker compose -f docker-compose.scaled.yml up -d --scale mitm-worker=8
```

This keeps rule isolation strong and bounds the impact of a failed worker. A coordinator restart does not require Redis for recovery: workers re-register and instance ownership is rediscovered on demand by querying their active instances.

If coordinator high availability is required later, the in-memory registry can be replaced with a shared store without changing the public instance API.

## API usage

### Add a response mock

```bash
curl -s -X POST http://localhost:8090/instances/{INSTANCE_ID}/rules \
  -H "Content-Type: application/json" \
  -d '{
    "match": { "urlContains": "example.com/api" },
    "action": {
      "modifyResponse": {
        "statusCode": 200,
        "body": {"mocked": true}
      }
    }
  }'
```

### Download the instance CA

```bash
curl -s http://localhost:8090/instances/{INSTANCE_ID}/cert -o ca.pem
```

### Destroy the instance

```bash
curl -X DELETE 'http://localhost:8090/instances/{INSTANCE_ID}?cleanup=true'
```

### Key endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Grid mode, version, capacity, and worker health. |
| `GET` | `/workers` | Registered workers. Coordinator mode only. |
| `POST` | `/workers/register` | Worker heartbeat/registration. Coordinator mode only. |
| `GET` | `/instances` | List active instances. Coordinator aggregates all workers. |
| `POST` | `/instances` | Allocate a proxy instance. |
| `GET` | `/instances/{id}` | Get instance details. |
| `DELETE` | `/instances/{id}` | Destroy an instance. |
| `POST` | `/instances/{id}/renew` | Renew TTL. |
| `GET` | `/instances/{id}/rules` | List interception rules. |
| `POST` | `/instances/{id}/rules` | Add a rule. |
| `DELETE` | `/instances/{id}/rules/{index}` | Delete a rule. |
| `PATCH` | `/instances/{id}/rules/{index}/toggle` | Toggle a rule. |
| `GET` | `/instances/{id}/cert` | Download the instance CA certificate. |

## Versioning and GHCR releases

Pull requests run Python unit tests, compile the Python sources, and build the multi-architecture container without publishing it.

A GHCR image is published **only** when a semantic Git tag is pushed:

```bash
git tag v2.1.0
git push origin v2.1.0
```

That tag publishes exactly:

```text
ghcr.io/ygrip/mitmproxy-grid:2.1.0
```

No `main`, SHA, major-only, minor-only, or `latest` package tag is published by the release workflow. This keeps automation environments reproducible and lets clients declare the exact grid version they support.

## Build locally

```bash
docker build \
  --build-arg GRID_VERSION=dev \
  -t mitmproxy-grid:dev \
  ./grid
```

## Project structure

```text
├── .github/workflows/publish-ghcr.yml
├── docker-compose.yml
├── docker-compose.scaled.yml
├── Makefile
└── grid/
    ├── Dockerfile
    ├── config.py
    ├── main.py
    ├── models.py
    ├── instance_manager.py
    ├── worker_registry.py
    ├── rule_registry.py
    ├── interceptor_template.py
    ├── dashboard.html
    └── tests/
        └── test_worker_registry.py
```

## License

MIT
