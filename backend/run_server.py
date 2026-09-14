"""Production-friendly launcher: runs uvicorn with TLS if certs/fullchain.pem
and certs/privkey.pem exist (uploaded via /api/settings/tls/certificate, or
generated with scripts/generate_self_signed_cert.py), otherwise plain HTTP.

Usage: python run_server.py  (from the backend/ directory)
Env vars: HOST (default 0.0.0.0), PORT (default 8010), CERT_DIR (default certs),
          RELOAD=1 for autoreload during development.
"""
import os
from pathlib import Path

import uvicorn

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8010"))
CERT_DIR = Path(os.getenv("CERT_DIR", "certs"))
CERT_FILE = CERT_DIR / "fullchain.pem"
KEY_FILE = CERT_DIR / "privkey.pem"

if __name__ == "__main__":
    kwargs = {
        "host": HOST,
        "port": PORT,
        "reload": os.getenv("RELOAD") == "1",
    }
    if CERT_FILE.exists() and KEY_FILE.exists():
        kwargs["ssl_certfile"] = str(CERT_FILE)
        kwargs["ssl_keyfile"] = str(KEY_FILE)
        print(f"[run_server] TLS enabled - serving https://{HOST}:{PORT}")
    else:
        print(f"[run_server] No certs found in {CERT_DIR}/ - serving http://{HOST}:{PORT}")
        print("[run_server] Upload a cert on Settings > Security, or run "
              "scripts/generate_self_signed_cert.py for LAN-only HTTPS.")

    uvicorn.run("main:app", **kwargs)
