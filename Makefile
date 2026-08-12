COMPOSE ?= docker compose
IMAGE ?= ghcr.io/ygrip/mitmproxy-grid
WORKERS ?= 4

.PHONY: start start-scaled pull pull-scaled build stop stop-scaled clean logs logs-scaled wait-ready contract

start: pull
	$(COMPOSE) up -d

start-scaled: pull-scaled
	$(COMPOSE) -f docker-compose.scaled.yml up -d --scale mitm-worker=$(WORKERS)

pull:
	$(COMPOSE) pull

pull-scaled:
	$(COMPOSE) -f docker-compose.scaled.yml pull

build:
	docker build --build-arg GRID_VERSION=dev -t $(IMAGE):dev ./grid

stop:
	$(COMPOSE) down

stop-scaled:
	$(COMPOSE) -f docker-compose.scaled.yml down

logs:
	$(COMPOSE) logs -f

logs-scaled:
	$(COMPOSE) -f docker-compose.scaled.yml logs -f

clean:
	$(COMPOSE) down -v

wait-ready:
	@until curl -s http://localhost:8090/health | grep -E 'UP|DEGRADED'; do sleep 2; done
	@echo "Grid API reachable."

contract:
	@curl -s http://localhost:8090/openapi.json | python3 -m json.tool > openapi.json
	@echo "OpenAPI contract saved to openapi.json"
