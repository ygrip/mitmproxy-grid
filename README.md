# mitmproxy Grid

A self-hosted HTTP proxy grid that manages multiple [mitmproxy](https://mitmproxy.org/) instances through a REST API. Spin up isolated proxy instances on demand, define interception rules to modify requests and responses on the fly, and tear them down when you're done, all via a single control plane.

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
- **GHCR image** — Prebuilt `linux/amd64` and `linux/arm64` images published to GitHub Container Registry.

## Installation

The published image is:

```text
ghcr.io/ygrip/mitmproxy-grid:latest
```

### Run directly with Docker

No repository clone is required:

```bash
docker run -d \
  --name mitmproxy-grid \
  --restart unless-stopped \
  -p 8090:8090 \
  -p 10000-10100:10000-10100 \
  -e INSTANCE_TTL=1800 \
  -e MAX_BODY_SIZE=52428800 \
  -v mitmproxy-grid-data:/data \
  -v mitmproxy-grid-ca:/ca \
  ghcr.io/ygrip/mitmproxy-grid:latest
```

Verify the grid:

```bash
curl http://localhost:8090/health
```

Then open:

- Dashboard: `http://localhost:8090`
- Swagger UI: `http://localhost:8090/docs`
- ReDoc: `http://localhost:8090/redoc`

To stop and remove the container while keeping its data volumes:

```bash
docker rm -f mitmproxy-grid
```

### Run with Docker Compose

Clone the repository if you prefer Compose-managed configuration:

```bash
git clone https://github.com/ygrip/mitmproxy-grid.git
cd mitmproxy-grid
docker compose up -d
```

The Compose file pulls `ghcr.io/ygrip/mitmproxy-grid:latest` and creates persistent Docker volumes automatically. No `.env` file is required for the defaults.

To use a specific image version:

```bash
MITMPROXY_GRID_VERSION=2.1.0 docker compose up -d
```

Optional runtime configuration:

| Variable | Default | Description |
|---|---:|---|
| `INSTANCE_TTL` | `1800` | Default instance lifespan in seconds |
| `MAX_BODY_SIZE` | `52428800` | Maximum decoded `bodyBase64` payload size in bytes |
| `MITMPROXY_GRID_VERSION` | `latest` | Image tag used by Docker Compose |

For example:

```bash
INSTANCE_TTL=3600 MAX_BODY_SIZE=104857600 docker compose up -d
```

### Private GHCR access

If the package is private, authenticate before pulling it:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
docker pull ghcr.io/ygrip/mitmproxy-grid:latest
```

The token needs `read:packages`. Public packages can be pulled without authentication.

## Image tags and publishing

GitHub Actions builds the image on pull requests and publishes it to GHCR from `main` and version tags.

Published tags include:

- `latest` for the default branch
- `main` for the default branch build
- `sha-<commit>` for commit-addressable builds
- `2.1.0`, `2.1`, and `2` for a Git tag such as `v2.1.0`

The image is built for both `linux/amd64` and `linux/arm64`.

> **Maintainer note:** GitHub Container Registry packages may initially be private. After the first successful publish, set the package visibility to **Public** if you want users to pull `ghcr.io/ygrip/mitmproxy-grid` without logging in.

## Build locally

To build the image from source instead of using GHCR:

```bash
git clone https://github.com/ygrip/mitmproxy-grid.git
cd mitmproxy-grid
docker build -t mitmproxy-grid:dev ./grid
```

Or use the Makefile:

```bash
make build
```

`make start` pulls the current GHCR image and starts the Compose stack.

## Usage

### Quick start

```bash
# 1. Create a proxy instance
curl -s -X POST http://localhost:8090/instances | python3 -m json.tool

# 2. Add an interception rule (replace INSTANCE_ID)
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

# 4. Route traffic through the allocated proxy port returned by step 1
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
make start      # Pull the GHCR image and start the grid
make pull       # Pull the configured image
make build      # Build a local development image
make stop       # Stop the grid
make logs       # Follow container logs
make clean      # Stop and remove Compose volumes
make wait-ready # Wait until /health reports UP
make contract   # Export the OpenAPI spec to openapi.json
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

```text
├── .github/workflows/
│   └── publish-ghcr.yml  # Multi-arch GHCR build and publish workflow
├── docker-compose.yml    # Run the published container image
├── Makefile              # Convenience commands
├── .env.example          # Optional environment defaults
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
