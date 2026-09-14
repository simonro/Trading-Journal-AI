"""Generate a self-signed TLS certificate for LAN-only HTTPS (e.g. a home
server your Mac/iOS/web clients reach over the local network). Browsers will
warn about the self-signed cert on first connect; trust it locally, or use a
real cert (Let's Encrypt, or your router/NAS CA) uploaded through
Settings > Security for a warning-free experience.

Usage: python scripts/generate_self_signed_cert.py [hostname_or_ip ...]
Writes backend/certs/fullchain.pem and backend/certs/privkey.pem.
"""
import ipaddress
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

OUT_DIR = Path(__file__).resolve().parent.parent / "backend" / "certs"


def _san_entries(names):
    entries = []
    for n in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            entries.append(x509.DNSName(n))
    return entries


def main():
    hostnames = sys.argv[1:] or ["localhost", "127.0.0.1"]
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0]),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Trading Journal AI (self-signed)"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(_san_entries(hostnames)), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "fullchain.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (OUT_DIR / "privkey.pem").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    print("Wrote " + str(OUT_DIR / "fullchain.pem") + " and " + str(OUT_DIR / "privkey.pem"))
    print("Covers: " + ", ".join(hostnames) + ". Restart the backend (run_server.py) to pick it up.")


if __name__ == "__main__":
    main()
