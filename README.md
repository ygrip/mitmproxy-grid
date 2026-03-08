# mitmproxy Grid

A self-hosted HTTP proxy grid that manages multiple [mitmproxy](https://mitmproxy.org/) instances through a REST API. Spin up isolated proxy instances on demand, define interception rules to modify requests and responses on the fly, and tear them down when you're done — all via a single control plane.

## Features

- **Multi-instance management** — Create, list, inspect, renew, and destroy mitmproxy instances via REST endpoints. Each instance runs on its own port with independent configuration.
- **Automatic TTL & expiry** — Every instance has a configurable time-to-live (default 30 min). A background reaper automatically cleans up expired instances and their data.
- **Rule-based HTTP interception** — Declarative JSON rules that match flows by URL pattern/substring, HTTP method, or content type, then apply actions to requests, responses, or both.
- **Request modification** — Set or remove headers, add or remove query parameters, replace request bodies (text, base64, or binary file).
- **Response modification** — Override status codes, modify headers, replace or patch response bodies. Supports binary payloads (images, fonts, etc.) via base64 with automatic disk externalization for large files.
- **Per-instance CA certificates** — Each proxy instance generates its own CA, downloadable via the API for easy client configuration.
- **Client IP tracking** — Tracks unique client IPs connecting through each proxy instance.
- **Web dashboard** — Built-in HTML dashboard at the root URL for visual instance management.
- **OpenAPI documentation** — Auto-generated Swagger UI at `/docs` and ReDoc at `/redoc`.
- **Docker-based deployment** — Single `docker-compose up` to run the entire grid.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/)
- `curl` (optional, for health checks and contract generation)

## Setup

1. **Clone the repository**

   ```bash
   git clone git@github.com-personal:ygrip/mitmproxy-grid.git
   cd mitmproxy-grid
   ```

2. **Configure environment**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` to adjust settings:

   | Variable | Default | Description |
   |---|---|---|
   | `INSTANCE_TTL` | `1800` | Instance lifespan in seconds (30 min) |
   | `MAX_BODY_SIZE` | `52428800` | Max decoded body size for base64 payloads (50 MB) |

3. **Start the grid**

   ```bash
   make start
   ```

   This builds the Docker image and starts the container. The API will be available at `http://localhost:8090`.

4. **Verify it's running**

   ```bash
   make wait-ready
   ```

   Or manually:

   ```bash
   curl http://localhost:8090/health
   ```

## Usage

### Quick start

```bash
# 1. Create a proxy instance
curl -s -X POST http://localhost:8090/instances | python3 -m json.tool

# 2. Add an interception rule (replace INSTANCE_ID and PORT)
curl -s -X POST http://localhost:8090/instances/{INSTANCE_ID}/rules \
  -H "Content-Type: application/json" \
  -d '{
    "match": { "urlContains": "example.com" },
    "action": {
      "modifyResponse": {
        "statusCode": 200,
        "body": "{\"mocked\": true}"
      }
    }
  }'

# 3. Download the CA certificate for your client
curl -s http://localhost:8090/instances/{INSTANCE_ID}/cert -o ca.pem

# 4. Route traffic through the proxy
curl -x http://localhost:{PORT} --cacert ca.pem https://example.com/api
```

### Rule examples

**Inject a debug header into all GET requests:**

```json
{
  "match": { "method": "GET" },
  "action": {
    "modifyRequest": {
      "headers": { "set": { "X-Debug": "1" } }
    }
  }
}
```

**Mock an API response with a binary image:**

```json
{
  "match": { "urlContains": "avatar.png" },
  "action": {
    "modifyResponse": {
      "statusCode": 200,
      "headers": { "set": { "Content-Type": "image/png" } },
      "bodyBase64": "iVBORw0KGgoAAAANSUhEUg..."
    }
  }
}
```

**Find-and-replace in response body:**

```json
{
  "match": { "urlPattern": ".*\\/api\\/.*" },
  "action": {
    "modifyResponse": {
      "bodyReplace": { "from_": "prod-host", "to": "staging-host" }
    }
  }
}
```

### Other commands

```bash
make stop     # Stop the grid
make logs     # Follow container logs
make clean    # Stop and remove volumes
make contract # Export the OpenAPI spec to openapi.json
```

## API Reference

Full interactive documentation is available at:

- **Swagger UI**: `http://localhost:8090/docs`
- **ReDoc**: `http://localhost:8090/redoc`
- **OpenAPI JSON**: `http://localhost:8090/openapi.json`

### Key endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Grid health and capacity |
| `GET` | `/instances` | List all instances |
| `POST` | `/instances` | Create a new instance |
| `GET` | `/instances/{id}` | Instance detail with rules |
| `DELETE` | `/instances/{id}` | Destroy an instance |
| `POST` | `/instances/{id}/renew` | Extend instance TTL |
| `GET` | `/instances/{id}/rules` | List rules |
| `POST` | `/instances/{id}/rules` | Add a rule |
| `DELETE` | `/instances/{id}/rules/{index}` | Remove a rule |
| `PATCH` | `/instances/{id}/rules/{index}/toggle` | Enable/disable a rule |
| `GET` | `/instances/{id}/cert` | Download CA certificate |

## Project Structure

```
├── docker-compose.yml    # Container orchestration
├── Makefile              # Convenience commands
├── .env.example          # Environment variable template
├── openapi.json          # Exported OpenAPI contract
└── grid/
    ├── Dockerfile            # mitmproxy + FastAPI image
    ├── main.py               # FastAPI app and route handlers
    ├── models.py             # Pydantic request/response models
    ├── instance_manager.py   # Instance lifecycle management
    ├── rule_registry.py      # Rule CRUD and blob externalization
    ├── interceptor_template.py  # mitmproxy addon (request/response hooks)
    └── dashboard.html        # Web dashboard UI
```

## License

MIT
