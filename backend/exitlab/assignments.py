"""Separate strategy and auto-setup membership from semantic policy hashes."""
import json
from collections import Counter

AUTO_LABELS = {
    "A": "Flush Reclaim (A)", "B": "Leader Long (B)",
    "B_LITE": "Leader Long Lite (B_LITE)", "C": "Laggard Short (C)",
    "NONE": "No setup",
}


def columns(conn, table):
    # Table identifiers are internal constants, never request input.
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def named(value):
    return value if isinstance(value, str) and value.strip() else None


def setup_name(row):
    strategy = named(row.get("strategy"))
    auto = named(row.get("setup"))
    return strategy or (AUTO_LABELS.get(auto, auto) if auto else None)


def applies(rule, row):
    return rule._applies_to_setups is None or any(
        row.get("strategy" if item["group"] == "strategy" else "setup") == item["name"]
        for item in rule._applies_to_setups)


def journal_rows(conn, account_id):
    analysis = columns(conn, "trade_analysis")
    strategy = "NULL"
    if {"strategy", "trade_group"} <= analysis:
        order = " ORDER BY a.id DESC" if "id" in analysis else ""
        strategy = f"(SELECT a.strategy FROM trade_analysis a WHERE a.trade_group=t.trade_group{order} LIMIT 1)"
    return conn.execute(f"SELECT t.*, {strategy} AS strategy FROM trades t "
                        "WHERE t.account_id=? ORDER BY t.date,t.id", (account_id,)).fetchall()


def setup_options(conn, account_id, entries=None):
    from .service import load_entries
    entries = entries if entries is not None else load_entries(conn, account_id, "sip")
    counts = {group: Counter() for group in ("strategy", "auto")}
    eligible = {group: Counter() for group in counts}
    for entry in entries:
        for group, field in (("strategy", "strategy"), ("auto", "setup")):
            name = named(entry["journal_trade"].get(field))
            if name:
                counts[group][name] += 1
                if not entry["reasons"]:
                    eligible[group][name] += 1
    if {"kind", "name"} <= columns(conn, "library_items"):
        for row in conn.execute("SELECT name FROM library_items WHERE kind='strategy'"):
            if named(row[0]):
                counts["strategy"].setdefault(row[0], 0)
    if {"name", "active"} <= columns(conn, "custom_setups"):
        for row in conn.execute("SELECT name FROM custom_setups WHERE active=1"):
            if named(row[0]):
                counts["strategy"].setdefault(row[0], 0)
    return [{"group": group, "label": label, "items": [
        {"name": name, "trades": counts[group][name], "eligible": eligible[group][name]}
        for name in sorted(counts[group], key=lambda n: (-counts[group][n], n.casefold(), n))
    ]} for group, label in (("strategy", "Strategies"), ("auto", "Auto setups"))]


def migrate_assignments(conn):
    auto_names = {r[0] for r in conn.execute("SELECT DISTINCT setup FROM trades")} if "setup" in columns(conn, "trades") else set()
    for table in ("exit_rules",):
        for row in conn.execute(f"SELECT id,applies_to_setups FROM {table}").fetchall():
            old = json.loads(row[1])
            if old is not None and any(isinstance(item, str) for item in old):
                new = [{"group": "auto" if item in auto_names else "strategy", "name": item}
                       if isinstance(item, str) else item for item in old]
                # Keep the same canonical ordering as the assignment API.
                unique = {(item["group"], item["name"]): item for item in new}
                new = [unique[key] for key in sorted(unique)]
                conn.execute(f"UPDATE {table} SET applies_to_setups=? WHERE id=?", (json.dumps(new), row[0]))


def saved_rule(row):
    from .rules import Rule
    rule = Rule.model_validate_json(row["json"])
    assignment = json.loads(row["applies_to_setups"])
    rule._applies_to_setups = tuple(assignment) if assignment is not None else None
    rule._saved_id = row["id"]
    return rule
