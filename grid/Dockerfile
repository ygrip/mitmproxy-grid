FROM mitmproxy/mitmproxy:latest

WORKDIR /app

COPY main.py models.py instance_manager.py rule_registry.py interceptor_template.py dashboard.html ./

RUN pip install --no-cache-dir fastapi uvicorn

EXPOSE 8090

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
