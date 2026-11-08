# The FastAPI backend. Built by docker-compose.yml from the repo root.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# VERSION sits one level above backend/, where update_check.py looks for it.
COPY VERSION /app/VERSION
COPY backend/ ./

# Everything the journal writes (database, its backups, uploads, the update-check cache) goes to
# /data, which docker-compose.yml keeps in the journal-data volume.
RUN useradd --create-home --uid 10001 journal \
    && mkdir -p /data/uploads \
    && chown -R journal:journal /data
ENV DATABASE_PATH=/data/trading_journal.db \
    UPLOAD_DIR=/data/uploads \
    UPDATE_CHECK_CACHE=/data/update_check.json \
    JOURNAL_INSTALL=docker
USER journal
VOLUME /data

EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/api/update', timeout=4)" || exit 1
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
