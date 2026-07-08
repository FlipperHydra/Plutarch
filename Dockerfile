FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLUTARCH_DATA_DIR=/app/data

WORKDIR /app

# System packages: build tools kept minimal; add nvidia-smi via runtime if needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/backend/requirements.txt

COPY backend  /app/backend
COPY frontend /app/frontend

# Data volume lives at /app/data (a single directory - see simple-agent notes).
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

WORKDIR /app/backend
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
