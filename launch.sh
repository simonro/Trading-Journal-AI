#!/usr/bin/env bash
# Starts Trading Journal AI on Mac and Linux. Run ./setup.sh once first.
# Backend: http://localhost:8010   Frontend: http://localhost:3010   Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ] || [ ! -d "frontend/node_modules" ]; then
  echo "Run ./setup.sh first; it installs what the journal needs."
  exit 1
fi

BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_PORT="${PORT:-3010}"

echo "Starting Trading Journal AI..."
(cd backend && exec ../.venv/bin/python -m uvicorn main:app --reload --port "$BACKEND_PORT") &
BACKEND_PID=$!

stop() {
  kill "$BACKEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
}
trap stop EXIT INT TERM

echo "Backend starting on http://localhost:$BACKEND_PORT"
echo "Frontend starting on http://localhost:$FRONTEND_PORT"
echo "Press Ctrl+C to stop both."
sleep 2
cd frontend
# HOST keeps the dev server on this computer only; the journal has no login.
HOST="${HOST:-127.0.0.1}" PORT="$FRONTEND_PORT" REACT_APP_API_URL="${REACT_APP_API_URL:-http://localhost:$BACKEND_PORT}" npm start
