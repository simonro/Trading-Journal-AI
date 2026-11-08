#!/usr/bin/env bash
# Trading Journal AI setup for Mac and Linux. Run once: ./setup.sh   then start with ./launch.sh
set -euo pipefail
cd "$(dirname "$0")"

fail() {
  echo
  echo "Setup stopped. $1"
  echo "Fix the problem above and run ./setup.sh again; finished steps are skipped or repeated safely."
  exit 1
}

echo "Trading Journal AI setup"
echo

PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/"
  echo "(Mac: brew install python@3.12, Ubuntu: sudo apt install python3 python3-venv)."
  fail "Python is missing or too old."
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js was not found. Install the LTS version from https://nodejs.org/"
  fail "Node.js is missing."
fi

echo "[1/4] Creating the Python environment in .venv"
if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON" -m venv .venv || fail "Could not create .venv (Ubuntu and Debian need: sudo apt install python3-venv)."
fi

echo "[2/4] Installing the backend packages"
.venv/bin/python -m pip install --quiet --upgrade pip || fail "pip could not update itself."
.venv/bin/python -m pip install --quiet -r backend/requirements.txt || fail "The backend packages did not install."

echo "[3/4] Installing the frontend packages (a few minutes the first time)"
(
  cd frontend
  if ! npm ci --no-audit --no-fund --loglevel=error; then
    # An older npm can reject a lock file written by a newer one; npm install reconciles it.
    echo "      Retrying with npm install"
    npm install --no-audit --no-fund --loglevel=error
  fi
) || fail "The frontend packages did not install."

echo "[4/4] Creating backend/.env for your optional API keys"
if [ -f backend/.env ]; then
  echo "      backend/.env already exists, left as it is"
else
  cp .env.example backend/.env
  echo "      Created. Add keys later if you want the AI features or trade charts; the journal works without them."
fi

echo
echo "Setup finished. Start the journal with ./launch.sh, then open http://localhost:3010"
echo "Your journal starts empty. Add an account, then import your broker's statement."
