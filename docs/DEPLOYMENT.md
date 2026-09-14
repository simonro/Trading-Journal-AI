# Running the journal as a backend service (VM or container)

This turns the journal from "one machine, one browser tab" into a small
always-on service: your Mac app, iOS app and any browser on your LAN (or over
the internet, if you choose to expose it) all talk to the same backend and
the same `trading_journal.db`.

## Option A: Docker Compose (recommended)

```bash
cp .env.example backend/.env   # fill in your API keys + BACKUP_PASSPHRASE
docker compose up -d --build
```

- Backend: `http://<host>:8010`
- Frontend: `http://<host>:3010`
- Data persists in named Docker volumes (`backend_data`, `backend_uploads`,
  `backend_backups`, `backend_certs`) so `docker compose down` never deletes
  your trades.

Point your Mac/iOS/web clients at `http://<host>:8010` (or `https://` once you
enable TLS below).

### Adding HTTPS

Two ways to get an encrypted connection, even on your own LAN:

1. **Direct to the backend** (simplest): Settings > Security in the web app,
   upload a certificate + private key (or generate a free self-signed one for
   LAN-only use, see below). Restart the `backend` container so
   `run_server.py` picks up the new cert:
   `docker compose restart backend`. The backend now serves
   `https://<host>:8010` directly.
2. **One shared HTTPS port for everything** (backend + frontend): run the
   optional reverse proxy, which terminates TLS on `:443` for both:
   `docker compose --profile proxy up -d`. It reads the same cert you
   uploaded to the backend (shared via the `backend_certs` volume), so upload
   once and both routes are covered.

For a LAN-only self-signed cert (no public domain needed):

```bash
python scripts/generate_self_signed_cert.py 192.168.1.50 my-nas.local
docker cp backend/certs/. <backend-container>:/app/backend/certs/
docker compose restart backend
```

Browsers/devices will warn on first connect to a self-signed cert; trust it
once per device. For a warning-free experience, use a real certificate (for
example Let's Encrypt, or one issued by your router/NAS) via the same upload
endpoint.

## Option B: Bare VM with systemd (no Docker)

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..
cp .env.example backend/.env   # fill in keys + BACKUP_PASSPHRASE
```

Serve the frontend build (`frontend/build/`) with any static file server or
nginx, pointed at `REACT_APP_API_URL=https://<host>:8010`. Run the backend as
a service:

```bash
sudo cp deploy/trading-journal-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-journal-backend
```

`run_server.py` auto-detects `backend/certs/fullchain.pem` + `privkey.pem` and
serves HTTPS directly when present (uploaded via Settings > Security, or
generated with `scripts/generate_self_signed_cert.py`).

## Encrypted backups (scp / FTP / rsync) + schedule

Settings > Backup lets you:

1. Add a destination (scp/SFTP, FTP, or rsync - host, credentials, remote
   path). "Test connection" verifies it before you rely on it.
2. Set a schedule per destination (hourly/daily/weekly).
3. Click **Backup now** for a 1-click encrypted backup (works even with zero
   destinations configured - it always keeps an encrypted copy locally under
   `backups/`).

Every backup is a `tar.gz` of the SQLite database plus your uploaded
diary/screenshot files, encrypted with a passphrase-derived key
(`BACKUP_PASSPHRASE` in `backend/.env`) before it ever touches disk or leaves
the machine, so any remote destination only ever receives ciphertext.

### Testing disaster recovery (do this once, before you need it)

1. Settings > Backup > **Backup now**.
2. Settings > Backup > **Restore**, pick that same backup file, enter the
   passphrase.
3. The running instance is restored in place: it takes a safety copy of
   whatever was live first, then swaps in the restored database and uploads,
   and verifies the restored database with `PRAGMA integrity_check` before
   declaring success.
4. Confirm your trades are back (or unchanged, if you restored the same
   backup) on the Trades page.

This is safe to rehearse against a real environment: the pre-restore safety
copy means "restore the wrong thing" is itself one more restore away from
being undone.

## Reconciliation

Settings > Reconciliation (also linked from Trades):

- **Fees**: the journal estimates commissions when your broker's CSV export
  omits them. Enter (or bulk-import via CSV) your broker's actual per-trade
  fee to see the variance trade by trade and in aggregate.
- **Dividends**: log dividend payments manually (date, ticker, amount) since
  they don't come through the executions CSV import.

## FIFO / LIFO lot matching

Settings > Lot Matching lets you pick the account-wide default (FIFO or
LIFO) used to assign which opening lot each closing fill is considered to
close. Changing it **recalculates every trade's lot breakdown** immediately;
your trades' realized P&L numbers do not change (the whole round trip nets
out the same either way), only which lot is matched to which exit, and
therefore each lot's holding period (short-term vs. long-term) and per-lot
cost basis, does.

On an individual trade's detail page you can override the method for just
that trade (useful when your broker allows specific-lot identification and
you told them which lot to sell); this is what the IRS calls "specific
identification" and it's the one case where you may deviate from your
account's default FIFO/LIFO election. See `backend/lot_matching.py` for the
matching algorithm and its regulatory notes.
