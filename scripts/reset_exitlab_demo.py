"""Build a fresh demo from a local price cache, with only price data copied."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT/"backend"/"exitlab_demo.db")
    parser.add_argument("--cache", type=Path, default=ROOT/"backend"/"exitlab_local.db")
    parser.add_argument("--replace", action="store_true", help="Replace only a verified synthetic Exit Lab demo database")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--rules-from", type=Path, default=ROOT/"backend"/"exitlab_demo.db", help="Read-only source of saved rules, visibility and setup assignments")
    args = parser.parse_args()
    dest, cache = args.database.resolve(), args.cache.resolve()
    if dest.parent != (ROOT/"backend").resolve() or not dest.name.startswith("exitlab_") or dest.suffix != ".db" or dest == cache:
        parser.error("Use a distinct local backend/exitlab_*.db destination")
    if not cache.is_file():
        parser.error("Price cache is missing; first run seed_exitlab_demo.py --fetch")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(dest)+suffix).exists():
            parser.error("Stop the demo server before resetting the database")
    if dest.exists():
        if not args.replace:
            parser.error("Destination exists; use --replace to rebuild a verified demo")
        with closing(sqlite3.connect(dest.as_uri()+"?mode=rw", uri=True)) as old:
            if old.execute("SELECT count(*) FROM trades WHERE source IS NULL OR source!='demo' OR trade_group NOT LIKE 'exitlab-demo-v2:%'").fetchone()[0]:
                parser.error("Refusing to replace a database with non-demo trades")
    # Stage beside the destination and swap only after a successful build.
    staging = dest.with_name(dest.stem+"_building.db")
    if staging.exists():
        parser.error("Staging database exists; inspect it before retrying")
    conn = None
    try:
        os.environ["DATABASE_PATH"] = str(staging)
        sys.path.insert(0, str(ROOT/"backend"))
        import database
        database.init_db()
        conn = database.get_db()
        with closing(sqlite3.connect(cache.as_uri()+"?mode=ro", uri=True)) as source:
            rows = source.execute("SELECT * FROM market_bars").fetchall()
            with conn:
                conn.executemany("INSERT INTO market_bars VALUES("+",".join("?" for _ in range(11))+")", rows)
        # Read through SQLite so an open source DB's WAL is included consistently.
        # No analysis runs, locks or cohort exposure are copied to the fresh demo.
        if args.rules_from.is_file():
            with closing(sqlite3.connect(args.rules_from.resolve().as_uri()+"?mode=ro", uri=True)) as source:
                source.row_factory = sqlite3.Row
                source.execute("BEGIN")
                saved = [dict(r) for r in source.execute("SELECT * FROM exit_rules ORDER BY id")]
                with conn:
                    for rule in saved:
                        if rule['is_preset']:
                            conn.execute("UPDATE exit_rules SET applies_to_setups=? WHERE is_preset=1 AND hash=?",
                                         (rule.get('applies_to_setups', 'null'), rule['hash']))
                        else:
                            conn.execute("""INSERT INTO exit_rules(id,name,json,hash,created_at,is_preset,show_in_tables,applies_to_setups)
                                VALUES(?,?,?,?,?,0,?,?)""", (rule['id'], rule['name'], rule['json'], rule['hash'], rule['created_at'],
                                rule.get('show_in_tables', 1), rule.get('applies_to_setups', 'null')))
                print(f"Preserved {sum(not r['is_preset'] for r in saved)} custom rules and preset assignments")
        conn.close()
        command = [sys.executable, str(ROOT/"scripts"/"seed_exitlab_demo.py"), "--database", str(staging)]
        if args.report:
            command += ["--report", str(args.report.resolve())]
        subprocess.run(command, check=True)
        with closing(sqlite3.connect(staging)) as check:
            check.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            check.execute("PRAGMA journal_mode=DELETE")
            if not check.execute("SELECT count(*) FROM trades").fetchone()[0]:
                raise SystemExit("No demo trades were built. Run scripts/seed_exitlab_demo.py --fetch first.")
        os.replace(staging, dest)
    except BaseException:
        # A failed attempt removes only its own staging files, so a re-run can start clean.
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(staging)+suffix).unlink(missing_ok=True)
        raise
    print(f"Fresh synthetic demo: {dest}")


if __name__ == "__main__":
    main()
