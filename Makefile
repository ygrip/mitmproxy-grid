COMPOSE ?= docker compose
IMAGE ?= ghcr.io/ygrip/mitmproxy-grid

.PHONY: start pull build stop clean logs wait-ready contract

start: pull
	$(COMPOSE) up -d

pull:
	$(COMPOSE) pull

build:
	docker build -t $(IMAGE):dev ./grid

stop:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

clean:
	$(COMPOSE) down -v

wait-ready:
	@until curl -s http://localhost:8090/health | grep UP; do sleep 2; done
	@echo "Grid ready."

contract:
	@curl -s http://localhost:8090/openapi.json | python3 -m json.tool > openapi.json
	@echo "OpenAPI contract saved to openapi.json"
