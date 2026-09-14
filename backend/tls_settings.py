"""SSL/TLS certificate upload + status, so LAN and remote connections to the
backend can be encrypted. Certs are stored under CERT_DIR and picked up by
run_server.py at process start (uvicorn needs certfile/keyfile at startup, so
a cert change takes effect on next restart - the API response says so).
"""
import os
import ssl
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, HTTPException, UploadFile, File

router = APIRouter(prefix="/api/settings/tls")

CERT_DIR = Path(os.getenv("CERT_DIR", "certs"))
CERT_FILE = CERT_DIR / "fullchain.pem"
KEY_FILE = CERT_DIR / "privkey.pem"


def _read_cert_info(cert_bytes: bytes) -> dict:
    cert = x509.load_pem_x509_certificate(cert_bytes)
    return {
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "not_valid_before": cert.not_valid_before_utc.isoformat(),
        "not_valid_after": cert.not_valid_after_utc.isoformat(),
        "is_expired": cert.not_valid_after_utc < datetime.now(timezone.utc),
        "serial_number": str(cert.serial_number),
    }


@router.get("/status")
def tls_status():
    if not CERT_FILE.exists() or not KEY_FILE.exists():
        return {"configured": False}
    try:
        info = _read_cert_info(CERT_FILE.read_bytes())
    except Exception as e:
        return {"configured": True, "error": f"Could not parse installed certificate: {e}"}
    info["configured"] = True
    info["restart_required"] = True
    return info


@router.post("/certificate")
async def upload_certificate(
    certificate: UploadFile = File(..., description="PEM certificate (or full chain)"),
    private_key: UploadFile = File(..., description="PEM private key"),
):
    cert_bytes = await certificate.read()
    key_bytes = await private_key.read()

    try:
        cert = x509.load_pem_x509_certificate(cert_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid certificate file: {e}")

    try:
        key = serialization.load_pem_private_key(key_bytes, password=None)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid private key file: {e}")

    cert_public_numbers = cert.public_key().public_numbers()
    key_public_numbers = key.public_key().public_numbers()
    if cert_public_numbers != key_public_numbers:
        raise HTTPException(status_code=400, detail="Certificate and private key do not match.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_cert, tmp_key = Path(tmp) / "cert.pem", Path(tmp) / "key.pem"
        tmp_cert.write_bytes(cert_bytes)
        tmp_key.write_bytes(key_bytes)
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(tmp_cert), str(tmp_key))
        except ssl.SSLError as e:
            raise HTTPException(status_code=400, detail=f"OpenSSL rejected this cert/key pair: {e}")

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    CERT_FILE.write_bytes(cert_bytes)
    KEY_FILE.write_bytes(key_bytes)
    try:
        os.chmod(KEY_FILE, 0o600)
    except Exception:
        pass

    info = _read_cert_info(cert_bytes)
    info["configured"] = True
    info["restart_required"] = True
    return info


@router.delete("/certificate")
def remove_certificate():
    if not CERT_FILE.exists() and not KEY_FILE.exists():
        return {"configured": False}
    CERT_FILE.unlink(missing_ok=True)
    KEY_FILE.unlink(missing_ok=True)
    return {"configured": False, "restart_required": True}
