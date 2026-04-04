FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/media-stack/scripts

WORKDIR /opt/media-stack

RUN pip install --no-cache-dir bcrypt docker kubernetes pyyaml requests

COPY scripts /opt/media-stack/scripts

EXPOSE 9100

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD wget -qO- http://localhost:9100/healthz || exit 1
