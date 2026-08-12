# syntax=docker/dockerfile:1

FROM mitmproxy/mitmproxy:latest

ARG GRID_VERSION=dev
ENV GRID_VERSION=${GRID_VERSION} \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/ygrip/mitmproxy-grid" \
      org.opencontainers.image.title="mitmproxy-grid" \
      org.opencontainers.image.description="Self-hosted mitmproxy instance grid with REST API and dashboard" \
      org.opencontainers.image.version="${GRID_VERSION}"

WORKDIR /app

COPY main.py models.py config.py instance_manager.py worker_registry.py rule_registry.py interceptor_template.py dashboard.html ./

RUN pip install --no-cache-dir --disable-pip-version-check fastapi uvicorn \
    && mkdir -p /data /ca

VOLUME ["/data", "/ca"]

EXPOSE 8090 10000-10100

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=2)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
