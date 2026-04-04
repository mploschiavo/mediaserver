FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/media-stack/scripts

WORKDIR /opt/media-stack

RUN pip install --no-cache-dir bcrypt docker kubernetes pyyaml requests

COPY scripts /opt/media-stack/scripts
COPY config/defaults /opt/media-stack/config/defaults
COPY bootstrap/media-stack.bootstrap.policy.yaml /opt/media-stack/bootstrap/media-stack.bootstrap.policy.yaml
COPY bootstrap/media-stack.bootstrap.catalog.yaml /opt/media-stack/bootstrap/media-stack.bootstrap.catalog.yaml

EXPOSE 9100

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD wget -qO- http://127.0.0.1:9100/healthz || exit 1
