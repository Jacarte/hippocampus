FROM python:3.11-slim as builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
  build-essential \
  libpq-dev \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --user --no-cache-dir -r requirements.txt



FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  PATH=/root/.local/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
  curl \
  libpq5 \
  && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /root/.local

COPY server.py .

RUN mkdir -p /var/lib/mem0 && \
  chmod 755 /var/lib/mem0

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "server.py"]
