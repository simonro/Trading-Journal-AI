FROM python:3.11-slim AS backend

WORKDIR /app/backend

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get update && apt-get install -y --no-install-recommends rsync openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ .

# Trades DB, uploads, backups and certs are volumes - never baked into the image.
VOLUME ["/app/backend/data", "/app/backend/uploads", "/app/backend/backups", "/app/backend/certs"]

ENV DATABASE_PATH=/app/backend/data/trading_journal.db \
    UPLOAD_DIR=/app/backend/uploads \
    BACKUP_LOCAL_DIR=/app/backend/backups \
    CERT_DIR=/app/backend/certs \
    HOST=0.0.0.0 \
    PORT=8010

EXPOSE 8010

CMD ["python", "run_server.py"]
