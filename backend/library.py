"""Settings > Library: manage strategy names, sources and tags.

The names themselves live on the trades (trade_analysis.strategy,
trade_analysis.idea_source, trade_tags). This module lists them with usage
counts and lets Simon add, describe, rename, merge and delete them.

Strategy and source are also often saved a second time as tags of type
"strategy" / "source" by the diary AI. Those two tag types are kept in sync
here and are not managed as tags.

Merging A into B rewrites every trade from A to B and remembers A as an alias of
B, so the next diary analysis that says A is saved as B (see apply_aliases).
"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_db

router = APIRouter(prefix="/api/library")

KINDS = ("strategy", "source", "tag")
# Tag types that mirror the strategy / source fields rather than being tags.
MIRRORED_TAG_TYPES = {"strategy": "strategy", "source": "source"}
TAG_TYPES = ("setup", "execution", "mistake", "emotion", "outcome")


def get_connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def init_library_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS library_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK(kind IN ('strategy','source','tag')),
            tag_type TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(kind, tag_type, name)
        );
        CREATE TABLE IF NOT EXISTS library_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            tag_type TEXT NOT NULL DEFAULT '',
            alias TEXT NOT NULL,
            canonical TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(kind, tag_type, alias)
        );
    """)
    conn.commit()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _clean(name):
    return (name or "").strip()


def _check_kind(kind, tag_type):
    if kind not in KINDS:
        raise HTTPException(400, f"Unknown kind '{kind}'")
    if kind == "tag":
        if tag_type not in TAG_TYPES:
            raise HTTPException(400, f"Unknown tag type '{tag_type}'")
        return tag_type
    return ""


def _usage(conn, kind, tag_type, name):
    if kind == "strategy":
        return conn.execute("SELECT COUNT(*) FROM trade_analysis WHERE strategy = ?", (name,)).fetchone()[0]
    if kind == "source":
        return conn.execute("SELECT COUNT(*) FROM trade_analysis WHERE idea_source = ?", (name,)).fetchone()[0]
    return conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT trade_group, account_id FROM trade_tags "
        "WHERE tag_type = ? AND tag_value = ?)",
        (tag_type, name),
    ).fetchone()[0]


def _exists(conn, kind, tag_type, name):
    if _usage(conn, kind, tag_type, name) > 0:
        return True
    return conn.execute(
        "SELECT 1 FROM library_items WHERE kind = ? AND tag_type = ? AND name = ?",
        (kind, tag_type, name),
    ).fetchone() is not None


def _rewrite(conn, kind, tag_type, old, new):
    """Point every trade that uses `old` at `new` (None clears it)."""
    if kind in ("strategy", "source"):
        col = "strategy" if kind == "strategy" else "idea_source"
        conn.execute(f"UPDATE trade_analysis SET {col} = ? WHERE {col} = ?", (new, old))
        mirror = MIRRORED_TAG_TYPES[kind]
        _rewrite_tags(conn, mirror, old, new)
    else:
        _rewrite_tags(conn, tag_type, old, new)


def _rewrite_tags(conn, tag_type, old, new):
    if new is None:
        conn.execute("DELETE FROM trade_tags WHERE tag_type = ? AND tag_value = ?", (tag_type, old))
        return
    # A trade that already carries `new` would end up with it twice: drop the old row there.
    conn.execute(
        """DELETE FROM trade_tags
           WHERE tag_type = ? AND tag_value = ?
             AND EXISTS (SELECT 1 FROM trade_tags n
                         WHERE n.tag_type = ? AND n.tag_value = ?
                           AND n.trade_group = trade_tags.trade_group
                           AND n.account_id IS trade_tags.account_id)""",
        (tag_type, old, tag_type, new),
    )
    conn.execute("UPDATE trade_tags SET tag_value = ? WHERE tag_type = ? AND tag_value = ?", (new, tag_type, old))


def _move_item(conn, kind, tag_type, old, new):
    """Carry the library entry (description) from old to new, keeping new's if it has one."""
    old_row = conn.execute(
        "SELECT description FROM library_items WHERE kind = ? AND tag_type = ? AND name = ?",
        (kind, tag_type, old),
    ).fetchone()
    new_row = conn.execute(
        "SELECT id, description FROM library_items WHERE kind = ? AND tag_type = ? AND name = ?",
        (kind, tag_type, new),
    ).fetchone()
    desc = old_row["description"] if old_row else None
    if new_row is None:
        conn.execute(
            "INSERT INTO library_items (kind, tag_type, name, description) VALUES (?,?,?,?)",
            (kind, tag_type, new, desc),
        )
    elif not new_row["description"] and desc:
        conn.execute("UPDATE library_items SET description = ? WHERE id = ?", (desc, new_row["id"]))
    conn.execute("DELETE FROM library_items WHERE kind = ? AND tag_type = ? AND name = ?", (kind, tag_type, old))


def _remember_alias(conn, kind, tag_type, alias, canonical):
    # Anything that used to resolve to the old name now resolves to the new one.
    conn.execute(
        "UPDATE library_aliases SET canonical = ? WHERE kind = ? AND tag_type = ? AND canonical = ?",
        (canonical, kind, tag_type, alias),
    )
    conn.execute(
        """INSERT INTO library_aliases (kind, tag_type, alias, canonical) VALUES (?,?,?,?)
           ON CONFLICT(kind, tag_type, alias) DO UPDATE SET canonical = excluded.canonical""",
        (kind, tag_type, alias, canonical),
    )
    conn.execute(
        "DELETE FROM library_aliases WHERE kind = ? AND tag_type = ? AND alias = canonical",
        (kind, tag_type),
    )


def apply_aliases(conn, analysis: dict):
    """Rewrite merged-away names in a fresh diary analysis before it is saved."""
    rows = conn.execute("SELECT kind, tag_type, alias, canonical FROM library_aliases").fetchall()
    if not rows:
        return analysis
    table = {(r["kind"], r["tag_type"], r["alias"]): r["canonical"] for r in rows}

    def resolve(kind, tag_type, value):
        if not value:
            return value
        return table.get((kind, tag_type, value.strip()), value)

    for ta in analysis.get("trade_analyses", []) or []:
        ta["strategy"] = resolve("strategy", "", ta.get("strategy"))
        ta["idea_source"] = resolve("source", "", ta.get("idea_source"))
        for tag in ta.get("tags", []) or []:
            t_type, value = tag.get("type"), tag.get("value")
            if t_type == "strategy":
                tag["value"] = resolve("strategy", "", value)
            elif t_type == "source":
                tag["value"] = resolve("source", "", value)
            else:
                tag["value"] = resolve("tag", t_type, value)
    return analysis


def library_names(conn, kind, tag_type=""):
    """Names defined in the library but maybe not used on any trade yet."""
    return [r["name"] for r in conn.execute(
        "SELECT name FROM library_items WHERE kind = ? AND tag_type = ? ORDER BY name",
        (kind, tag_type),
    ).fetchall()]


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
def list_library(conn: sqlite3.Connection = Depends(get_connection)):
    items = {}

    def add(kind, tag_type, name, count=0, description=None):
        key = (kind, tag_type, name)
        entry = items.setdefault(key, {"name": name, "description": None, "trades": 0})
        entry["trades"] = max(entry["trades"], count)
        if description:
            entry["description"] = description

    for r in conn.execute(
        "SELECT strategy AS name, COUNT(*) AS n FROM trade_analysis WHERE strategy IS NOT NULL AND TRIM(strategy) <> '' GROUP BY strategy"
    ):
        add("strategy", "", r["name"], r["n"])
    for r in conn.execute(
        "SELECT idea_source AS name, COUNT(*) AS n FROM trade_analysis WHERE idea_source IS NOT NULL AND TRIM(idea_source) <> '' GROUP BY idea_source"
    ):
        add("source", "", r["name"], r["n"])
    placeholders = ",".join("?" * len(TAG_TYPES))
    for r in conn.execute(
        f"""SELECT tag_type, name, COUNT(*) AS n FROM (
                SELECT DISTINCT tag_type, tag_value AS name, trade_group, account_id FROM trade_tags
                WHERE tag_type IN ({placeholders}) AND TRIM(tag_value) <> '')
            GROUP BY tag_type, name""",
        TAG_TYPES,
    ):
        add("tag", r["tag_type"], r["name"], r["n"])
    for r in conn.execute("SELECT kind, tag_type, name, description FROM library_items"):
        if r["kind"] == "tag" and r["tag_type"] not in TAG_TYPES:
            continue
        add(r["kind"], r["tag_type"], r["name"], 0, r["description"])

    aliases = {}
    for r in conn.execute("SELECT kind, tag_type, alias, canonical FROM library_aliases ORDER BY alias"):
        aliases.setdefault((r["kind"], r["tag_type"], r["canonical"]), []).append(r["alias"])

    def collect(kind, tag_type=""):
        out = []
        for (k, t, name), entry in items.items():
            if k == kind and t == tag_type:
                entry = dict(entry)
                entry["aliases"] = aliases.get((k, t, name), [])
                out.append(entry)
        return sorted(out, key=lambda e: (-e["trades"], e["name"].lower()))

    return {
        "strategies": collect("strategy"),
        "sources": collect("source"),
        "tags": {t: collect("tag", t) for t in TAG_TYPES},
        "tag_types": list(TAG_TYPES),
    }


class ItemCreate(BaseModel):
    kind: str
    tag_type: str = ""
    name: str
    description: str | None = None


@router.post("", status_code=201)
def create_item(data: ItemCreate, conn: sqlite3.Connection = Depends(get_connection)):
    tag_type = _check_kind(data.kind, data.tag_type)
    name = _clean(data.name)
    if not name:
        raise HTTPException(400, "Name is required")
    if _exists(conn, data.kind, tag_type, name):
        raise HTTPException(409, f"'{name}' already exists")
    conn.execute(
        "INSERT INTO library_items (kind, tag_type, name, description) VALUES (?,?,?,?)",
        (data.kind, tag_type, name, _clean(data.description) or None),
    )
    conn.commit()
    return {"name": name}


class ItemUpdate(BaseModel):
    kind: str
    tag_type: str = ""
    name: str
    new_name: str | None = None
    description: str | None = None


@router.put("")
def update_item(data: ItemUpdate, conn: sqlite3.Connection = Depends(get_connection)):
    """Edit the description and/or rename. Renaming onto an existing name is refused; use merge."""
    tag_type = _check_kind(data.kind, data.tag_type)
    name = _clean(data.name)
    new_name = _clean(data.new_name) if data.new_name is not None else name
    if not new_name:
        raise HTTPException(400, "Name is required")
    if new_name != name:
        if _exists(conn, data.kind, tag_type, new_name):
            raise HTTPException(409, f"'{new_name}' already exists. Use Merge to combine them.")
        _rewrite(conn, data.kind, tag_type, name, new_name)
        _move_item(conn, data.kind, tag_type, name, new_name)
        _remember_alias(conn, data.kind, tag_type, name, new_name)
    if data.description is not None:
        row = conn.execute(
            "SELECT id FROM library_items WHERE kind = ? AND tag_type = ? AND name = ?",
            (data.kind, tag_type, new_name),
        ).fetchone()
        desc = _clean(data.description) or None
        if row:
            conn.execute("UPDATE library_items SET description = ? WHERE id = ?", (desc, row["id"]))
        else:
            conn.execute(
                "INSERT INTO library_items (kind, tag_type, name, description) VALUES (?,?,?,?)",
                (data.kind, tag_type, new_name, desc),
            )
    conn.commit()
    return {"name": new_name, "trades": _usage(conn, data.kind, tag_type, new_name)}


class ItemMerge(BaseModel):
    kind: str
    tag_type: str = ""
    source_name: str
    target_name: str


@router.post("/merge")
def merge_items(data: ItemMerge, conn: sqlite3.Connection = Depends(get_connection)):
    """Merge A into B: every trade using A now uses B, and A is remembered as an alias of B."""
    tag_type = _check_kind(data.kind, data.tag_type)
    src, dst = _clean(data.source_name), _clean(data.target_name)
    if not src or not dst or src == dst:
        raise HTTPException(400, "Pick two different names")
    moved = _usage(conn, data.kind, tag_type, src)
    _rewrite(conn, data.kind, tag_type, src, dst)
    _move_item(conn, data.kind, tag_type, src, dst)
    _remember_alias(conn, data.kind, tag_type, src, dst)
    conn.commit()
    return {"moved": moved, "target": dst, "trades": _usage(conn, data.kind, tag_type, dst)}


class ItemDelete(BaseModel):
    kind: str
    tag_type: str = ""
    name: str
    reassign_to: str | None = None


@router.post("/delete")
def delete_item(data: ItemDelete, conn: sqlite3.Connection = Depends(get_connection)):
    """Delete a name. Trades using it are reassigned to `reassign_to`, or left blank."""
    tag_type = _check_kind(data.kind, data.tag_type)
    name = _clean(data.name)
    target = _clean(data.reassign_to) or None
    if target == name:
        raise HTTPException(400, "Pick a different name to reassign to")
    affected = _usage(conn, data.kind, tag_type, name)
    _rewrite(conn, data.kind, tag_type, name, target)
    if target:
        _move_item(conn, data.kind, tag_type, name, target)
        _remember_alias(conn, data.kind, tag_type, name, target)
    else:
        conn.execute("DELETE FROM library_items WHERE kind = ? AND tag_type = ? AND name = ?", (data.kind, tag_type, name))
        conn.execute("DELETE FROM library_aliases WHERE kind = ? AND tag_type = ? AND canonical = ?", (data.kind, tag_type, name))
    conn.commit()
    return {"affected": affected, "reassigned_to": target}
