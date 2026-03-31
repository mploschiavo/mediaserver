FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/media-stack/scripts

WORKDIR /opt/media-stack

COPY scripts /opt/media-stack/scripts
