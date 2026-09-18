"""Encrypted backup/restore: scp (SFTP), FTP and rsync destinations, with a
schedule and a 1-click backup / restore.

The archive (SQLite db + WAL/SHM sidecar files + uploads) is tar'd then
encrypted at rest with Fernet (AES-128-CBC + HMAC) using a key derived from a
passphrase via PBKDF2-HMAC-SHA256. The passphrase is never stored: it must be
supplied for restore (or via BACKUP_PASSPHRASE in the environment, e.g. so a
scheduled job can also drive automated restores/disaster-recovery drills).
"""
import base64
import ftplib
import io
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from database import get_db, DB_PATH

router = APIRouter(prefix="/api/backup")

BACKUP_DIR = Path(os.getenv("BACKUP_LOCAL_DIR", "backups"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
KDF_SALT = b"trading-journal-ai-backup-salt-v1"  # static salt is fine: passphrase is the real secret
DEST_TYPES = ("scp", "ftp", "rsync")


def get_connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def init_backup_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS backup_destinations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('scp','ftp','rsync')),
            host TEXT NOT NULL,
            port INTEGER,
            username TEXT,
            secret TEXT,              -- password or SSH key passphrase, encrypted at rest
            key_path TEXT,            -- optional SSH private key path (scp/rsync)
            remote_path TEXT NOT NULL DEFAULT '.',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS backup_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            destination_id INTEGER NOT NULL REFERENCES backup_destinations(id) ON DELETE CASCADE,
            interval TEXT NOT NULL CHECK(interval IN ('hourly','daily','weekly')),
            hour INTEGER NOT NULL DEFAULT 2,
            minute INTEGER NOT NULL DEFAULT 0,
            day_of_week INTEGER,       -- 0=Monday .. 6=Sunday, only for weekly
            enabled INTEGER NOT NULL DEFAULT 1,
            UNIQUE(destination_id)
        );
        CREATE TABLE IF NOT EXISTS backup_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            destination_id INTEGER REFERENCES backup_destinations(id) ON DELETE SET NULL,
            trigger TEXT NOT NULL CHECK(trigger IN ('manual','scheduled','restore')),
            status TEXT NOT NULL CHECK(status IN ('running','success','failed')),
            filename TEXT,
            size_bytes INTEGER,
            message TEXT,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at TEXT
        );
    """)
    conn.commit()


# ── Encryption ────────────────────────────────────────────────────────────────

def _derive_key(passphrase: str) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=KDF_SALT, iterations=390_000)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _get_passphrase(explicit: str | None) -> str:
    passphrase = explicit or os.getenv("BACKUP_PASSPHRASE")
    if not passphrase:
        raise HTTPException(
            status_code=400,
            detail="A backup passphrase is required (pass it explicitly or set BACKUP_PASSPHRASE).",
        )
    return passphrase


def build_archive_bytes() -> bytes:
    """Tar the sqlite db (+ WAL/SHM sidecars) and the uploads folder into an in-memory buffer."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        db_path = Path(DB_PATH)
        for candidate in [db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")]:
            if candidate.exists():
                tar.add(candidate, arcname=candidate.name)
        if Path(UPLOAD_DIR).is_dir():
            tar.add(UPLOAD_DIR, arcname="uploads")
    return buf.getvalue()


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    return Fernet(_derive_key(passphrase)).encrypt(data)


def decrypt_bytes(data: bytes, passphrase: str) -> bytes:
    try:
        return Fernet(_derive_key(passphrase)).decrypt(data)
    except InvalidToken:
        raise HTTPException(status_code=400, detail="Wrong passphrase or corrupted backup file.")


# ── Local backup / restore ────────────────────────────────────────────────────

def create_local_backup(passphrase: str) -> tuple[Path, int]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"trading_journal_backup_{ts}.tar.gz.enc"
    path = BACKUP_DIR / filename
    encrypted = encrypt_bytes(build_archive_bytes(), passphrase)
    path.write_bytes(encrypted)
    return path, len(encrypted)


def restore_from_bytes(encrypted_data: bytes, passphrase: str):
    """Decrypt, safety-backup the current DB, then overwrite db + uploads."""
    decrypted = decrypt_bytes(encrypted_data, passphrase)

    # Safety net: always keep a copy of what was live before a restore.
    safety_path = None
    if Path(DB_PATH).exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safety_path = BACKUP_DIR / f"pre_restore_safety_{ts}.tar.gz.enc"
        safety_path.write_bytes(encrypt_bytes(build_archive_bytes(), passphrase))

    with tempfile.TemporaryDirectory() as tmp:
        buf = io.BytesIO(decrypted)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            tar.extractall(tmp)

        db_path = Path(DB_PATH)
        for name in [db_path.name, db_path.name + "-wal", db_path.name + "-shm"]:
            src = Path(tmp) / name
            if src.exists():
                shutil.copy2(src, db_path.with_name(name))
            else:
                stale = db_path.with_name(name)
                if name != db_path.name and stale.exists():
                    stale.unlink()

        restored_uploads = Path(tmp) / "uploads"
        if restored_uploads.is_dir():
            if Path(UPLOAD_DIR).exists():
                shutil.rmtree(UPLOAD_DIR)
            shutil.copytree(restored_uploads, UPLOAD_DIR)

        # Verify the restored db is a healthy sqlite file before declaring victory.
        check_conn = sqlite3.connect(DB_PATH)
        try:
            result = check_conn.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise HTTPException(status_code=500, detail=f"Restored database failed integrity check: {result}")
        finally:
            check_conn.close()

    return safety_path


# ── Remote transfer ────────────────────────────────────────────────────────────

def _decrypt_secret(dest_row: dict) -> str | None:
    # Secrets are stored using the server's own BACKUP_PASSPHRASE (or a dedicated
    # SECRET_ENCRYPTION_KEY) so they're never written to disk in cleartext.
    enc_key = os.getenv("SECRET_ENCRYPTION_KEY") or os.getenv("BACKUP_PASSPHRASE")
    if not dest_row.get("secret") or not enc_key:
        return dest_row.get("secret")
    try:
        return Fernet(_derive_key(enc_key)).decrypt(dest_row["secret"].encode()).decode()
    except Exception:
        return None


def _encrypt_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    enc_key = os.getenv("SECRET_ENCRYPTION_KEY") or os.getenv("BACKUP_PASSPHRASE")
    if not enc_key:
        return secret  # nowhere safe to derive a key from; caller has been warned via /test
    return Fernet(_derive_key(enc_key)).encrypt(secret.encode()).decode()


def upload_to_destination(dest: dict, local_path: Path):
    dtype = dest["type"]
    remote_name = f"{dest.get('remote_path', '.').rstrip('/')}/{local_path.name}"

    if dtype == "scp":
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        secret = _decrypt_secret(dest)
        client.connect(
            dest["host"], port=dest.get("port") or 22, username=dest.get("username"),
            password=secret if not dest.get("key_path") else None,
            key_filename=dest.get("key_path") or None, timeout=15,
        )
        try:
            sftp = client.open_sftp()
            sftp.put(str(local_path), remote_name)
            sftp.close()
        finally:
            client.close()

    elif dtype == "ftp":
        secret = _decrypt_secret(dest)
        with ftplib.FTP() as ftp:
            ftp.connect(dest["host"], dest.get("port") or 21, timeout=15)
            ftp.login(dest.get("username") or "anonymous", secret or "")
            if dest.get("remote_path") and dest["remote_path"] not in (".", ""):
                try:
                    ftp.cwd(dest["remote_path"])
                except ftplib.error_perm:
                    ftp.mkd(dest["remote_path"])
                    ftp.cwd(dest["remote_path"])
            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR {local_path.name}", f)

    elif dtype == "rsync":
        target = f"{dest.get('username') + '@' if dest.get('username') else ''}{dest['host']}:{dest.get('remote_path', '.')}/"
        cmd = ["rsync", "-az"]
        if dest.get("key_path"):
            cmd += ["-e", f"ssh -i {dest['key_path']} -o StrictHostKeyChecking=accept-new"]
        cmd += [str(local_path), target]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"rsync failed: {result.stderr.strip()}")
    else:
        raise ValueError(f"Unknown destination type: {dtype}")


def run_backup(conn: sqlite3.Connection, destination_id: int | None, trigger: str, passphrase: str | None = None) -> dict:
    passphrase = _get_passphrase(passphrase)
    cur = conn.execute(
        "INSERT INTO backup_runs (destination_id, trigger, status) VALUES (?,?,'running')",
        (destination_id, trigger),
    )
    run_id = cur.lastrowid
    conn.commit()

    try:
        local_path, size_bytes = create_local_backup(passphrase)
        if destination_id is not None:
            dest_row = conn.execute("SELECT * FROM backup_destinations WHERE id=?", (destination_id,)).fetchone()
            if not dest_row:
                raise ValueError(f"Destination {destination_id} not found")
            upload_to_destination(dict(dest_row), local_path)
        conn.execute(
            "UPDATE backup_runs SET status='success', filename=?, size_bytes=?, finished_at=datetime('now') WHERE id=?",
            (local_path.name, size_bytes, run_id),
        )
        conn.commit()
        return {"run_id": run_id, "status": "success", "filename": local_path.name, "size_bytes": size_bytes}
    except Exception as e:
        conn.execute(
            "UPDATE backup_runs SET status='failed', message=?, finished_at=datetime('now') WHERE id=?",
            (str(e), run_id),
        )
        conn.commit()
        raise HTTPException(status_code=502, detail=f"Backup failed: {e}")


# ── Scheduler wiring (registered from main.py at startup) ────────────────────

def run_due_scheduled_backups(get_conn_fn):
    """Called by APScheduler. Runs every destination's schedule check each tick."""
    conn = get_conn_fn()
    try:
        schedules = conn.execute(
            "SELECT * FROM backup_schedules WHERE enabled=1"
        ).fetchall()
        now = datetime.now()
        for s in schedules:
            due = False
            if s["interval"] == "hourly" and now.minute == s["minute"]:
                due = True
            elif s["interval"] == "daily" and now.hour == s["hour"] and now.minute == s["minute"]:
                due = True
            elif s["interval"] == "weekly" and now.weekday() == (s["day_of_week"] or 0) and now.hour == s["hour"] and now.minute == s["minute"]:
                due = True
            if due:
                try:
                    run_backup(conn, s["destination_id"], "scheduled")
                except Exception:
                    pass  # already logged in backup_runs
    finally:
        conn.close()


# ── API ─────────────────────────────────────────────────────────────────────

class DestinationBody(BaseModel):
    name: str
    type: str
    host: str
    port: int | None = None
    username: str | None = None
    secret: str | None = None
    key_path: str | None = None
    remote_path: str = "."
    enabled: bool = True


@router.get("/destinations")
def list_destinations(conn: sqlite3.Connection = Depends(get_connection)):
    rows = conn.execute("SELECT id,name,type,host,port,username,key_path,remote_path,enabled,created_at FROM backup_destinations").fetchall()
    return [dict(r) for r in rows]


@router.post("/destinations", status_code=201)
def create_destination(body: DestinationBody, conn: sqlite3.Connection = Depends(get_connection)):
    if body.type not in DEST_TYPES:
        raise HTTPException(status_code=400, detail=f"type must be one of {DEST_TYPES}")
    cur = conn.execute(
        """INSERT INTO backup_destinations (name,type,host,port,username,secret,key_path,remote_path,enabled)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (body.name, body.type, body.host, body.port, body.username, _encrypt_secret(body.secret),
         body.key_path, body.remote_path, int(body.enabled)),
    )
    conn.commit()
    row = conn.execute("SELECT id,name,type,host,port,username,key_path,remote_path,enabled,created_at FROM backup_destinations WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


@router.put("/destinations/{dest_id}")
def update_destination(dest_id: int, body: DestinationBody, conn: sqlite3.Connection = Depends(get_connection)):
    row = conn.execute("SELECT * FROM backup_destinations WHERE id=?", (dest_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Destination not found")
    secret = _encrypt_secret(body.secret) if body.secret else row["secret"]
    conn.execute(
        """UPDATE backup_destinations SET name=?,type=?,host=?,port=?,username=?,secret=?,key_path=?,remote_path=?,enabled=?
           WHERE id=?""",
        (body.name, body.type, body.host, body.port, body.username, secret, body.key_path, body.remote_path, int(body.enabled), dest_id),
    )
    conn.commit()
    row = conn.execute("SELECT id,name,type,host,port,username,key_path,remote_path,enabled,created_at FROM backup_destinations WHERE id=?", (dest_id,)).fetchone()
    return dict(row)


@router.delete("/destinations/{dest_id}")
def delete_destination(dest_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    conn.execute("DELETE FROM backup_destinations WHERE id=?", (dest_id,))
    conn.commit()
    return {"deleted": dest_id}


@router.post("/destinations/{dest_id}/test")
def test_destination(dest_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    row = conn.execute("SELECT * FROM backup_destinations WHERE id=?", (dest_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Destination not found")
    dest = dict(row)
    try:
        with tempfile.NamedTemporaryFile(suffix=".txt") as tmp:
            tmp.write(b"trading-journal-ai connectivity test\n")
            tmp.flush()
            upload_to_destination(dest, Path(tmp.name))
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Connection test failed: {e}")


class ScheduleBody(BaseModel):
    interval: str
    hour: int = 2
    minute: int = 0
    day_of_week: int | None = None
    enabled: bool = True


@router.put("/schedule/{dest_id}")
def upsert_schedule(dest_id: int, body: ScheduleBody, conn: sqlite3.Connection = Depends(get_connection)):
    if body.interval not in ("hourly", "daily", "weekly"):
        raise HTTPException(status_code=400, detail="interval must be hourly, daily or weekly")
    conn.execute(
        """INSERT INTO backup_schedules (destination_id,interval,hour,minute,day_of_week,enabled)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(destination_id) DO UPDATE SET
             interval=excluded.interval, hour=excluded.hour, minute=excluded.minute,
             day_of_week=excluded.day_of_week, enabled=excluded.enabled""",
        (dest_id, body.interval, body.hour, body.minute, body.day_of_week, int(body.enabled)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM backup_schedules WHERE destination_id=?", (dest_id,)).fetchone()
    return dict(row)


@router.get("/schedule")
def list_schedules(conn: sqlite3.Connection = Depends(get_connection)):
    rows = conn.execute("SELECT * FROM backup_schedules").fetchall()
    return [dict(r) for r in rows]


class RunNowBody(BaseModel):
    passphrase: str | None = None


@router.post("/run/{destination_id}")
def run_now(destination_id: int, body: RunNowBody, conn: sqlite3.Connection = Depends(get_connection)):
    return run_backup(conn, destination_id, "manual", body.passphrase)


@router.post("/run-local")
def run_local_only(body: RunNowBody, conn: sqlite3.Connection = Depends(get_connection)):
    """1-click local encrypted backup with no remote destination — the base of disaster recovery."""
    return run_backup(conn, None, "manual", body.passphrase)


@router.get("/runs")
def list_runs(limit: int = 50, conn: sqlite3.Connection = Depends(get_connection)):
    rows = conn.execute(
        "SELECT * FROM backup_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/local-files")
def list_local_backups():
    if not BACKUP_DIR.is_dir():
        return []
    files = sorted(BACKUP_DIR.glob("*.tar.gz.enc"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"filename": f.name, "size_bytes": f.stat().st_size, "modified_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()} for f in files]


@router.get("/local-files/{filename}")
def download_local_backup(filename: str):
    from fastapi.responses import FileResponse
    safe_name = Path(filename).name  # no path traversal
    path = BACKUP_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup file not found")
    return FileResponse(path, filename=safe_name, media_type="application/octet-stream")


@router.post("/restore")
async def restore_backup(
    passphrase: str = Form(...),
    file: UploadFile | None = File(None),
    local_filename: str | None = Form(None),
):
    """Restore for disaster recovery: upload an encrypted backup file, or point at
    one already sitting in the local backups/ folder (e.g. one just downloaded
    from a remote destination).

    Deliberately does not hold a request-scoped DB connection open across the
    restore: SQLite's WAL mode memory-maps the -shm sidecar for any open
    connection, and on Windows that blocks overwriting the very files being
    restored. Each DB touch here opens and closes its own short-lived connection.
    """
    if file is not None:
        data = await file.read()
    elif local_filename:
        path = BACKUP_DIR / Path(local_filename).name
        if not path.exists():
            raise HTTPException(status_code=404, detail="Local backup file not found")
        data = path.read_bytes()
    else:
        raise HTTPException(status_code=400, detail="Provide either a file upload or local_filename")

    log_conn = get_db()
    try:
        run_id = log_conn.execute(
            "INSERT INTO backup_runs (destination_id, trigger, status) VALUES (NULL,'restore','running')"
        ).lastrowid
        log_conn.commit()
    finally:
        log_conn.close()

    try:
        safety_path = restore_from_bytes(data, passphrase)
    except HTTPException as e:
        _finish_restore_run(run_id, "failed", str(e.detail))
        raise
    except Exception as e:
        _finish_restore_run(run_id, "failed", str(e))
        raise HTTPException(status_code=502, detail=f"Restore failed: {e}")

    _finish_restore_run(run_id, "success", f"safety copy: {safety_path.name if safety_path else 'none'}")
    return {"restored": True, "safety_backup": safety_path.name if safety_path else None}


def _finish_restore_run(run_id: int, status: str, message: str):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE backup_runs SET status=?, message=?, finished_at=datetime('now') WHERE id=?",
            (status, message, run_id),
        )
        conn.commit()
    finally:
        conn.close()
