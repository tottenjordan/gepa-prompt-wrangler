FROM python:3.11-slim

# Pinned, not floored. These were `fastmcp>=2.0.0` and opentelemetry 1.40.0
# until 2026-09-08: a Cloud Run rebuild would have resolved fastmcp 4.x, which
# speaks the mcp 2.x protocol, while ADK's client is held under mcp<2. The
# servers would have come up healthy and been unable to talk to any agent.
# Versions track uv.lock; tests/test_pipeline_image_pins.py fails on drift.
RUN pip install --no-cache-dir \
    fastmcp==3.4.7 \
    opentelemetry-api==1.42.1 \
    opentelemetry-sdk==1.42.1 \
    opentelemetry-exporter-otlp-proto-grpc==1.42.1 \
    google-auth==2.57.1 \
    grpcio==1.83.1 \
    requests==2.33.0
COPY . .
EXPOSE 8003
CMD ["python", "-m", "server"]
