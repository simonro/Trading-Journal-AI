"""Schwab Trader API transactions -> the journal's common execution shape.

Edge Trader for Schwab sends raw Schwab TRADE transactions to the sync endpoint. This module
turns them into exactly the execution dicts the thinkorswim statement parser produces
(`parse_cash_balance_section` / `parse_futures_section_rows`), so a fill reaches the same
trade, with the same key and the same fingerprint, whether it came from a CSV or a sync:

- times in US Eastern, HH:MM:SS; dates as the statement prints them (M/D/YY)
- futures on Schwab's trade date (an evening fill belongs to the next session), ticker without
  the exchange suffix ('/ESZ26:XCME' -> '/ESZ26')
- a vertical (two option legs of one order in the same second) is ONE fill at the net price,
  strikes in thinkorswim's order (the leg traded the vertical's way first)
- any other multi-leg order is one fill per leg, like the statement's Trade History
- fees: every fee item of the transaction, summed per fill (a vertical carries both legs')
- prices rounded to 5 decimals, as thinkorswim prints them

Each fill also carries `_fill_keys` (the Schwab activityIds behind it) and `_order_id`.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Option roots thinkorswim prints differently from the API's underlying symbol.
OPTION_ROOTS = {"SPXW": "SPX", "NDXP": "NDX", "RUTW": "RUT", "XSP": "XSP"}

_OCC = re.compile(r"^(?P<root>[A-Z.]+)\s*(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<strike>\d{8})$")


class AdapterError(ValueError):
    """A transaction the adapter cannot turn into a fill safely."""


def _parse_time(value: str) -> datetime:
    if not value:
        raise AdapterError("transaction without a time")
    s = value.replace("Z", "+0000")
    if re.search(r"[+-]\d{2}:\d{2}$", s):
        s = s[:-3] + s[-2:]
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise AdapterError(f"unreadable time {value!r}")


def _raw(d) -> str:
    return f"{d.month}/{d.day}/{str(d.year)[2:]}"


def _price(p) -> float:
    return round(float(p or 0.0), 5)


def _fees(txn: dict) -> float:
    total = 0.0
    for item in txn.get("transferItems") or []:
        ins = item.get("instrument") or {}
        if ins.get("assetType") == "CURRENCY":
            total += abs(float(item.get("cost") or 0.0))
    return round(total, 2)


def _legs(txn: dict) -> list[dict]:
    return [i for i in (txn.get("transferItems") or [])
            if (i.get("instrument") or {}).get("assetType") not in (None, "CURRENCY")]


def fill_key(txn: dict, leg_index: int = 0) -> str:
    """Stable key of one Schwab fill. Single-leg transactions (all real ones seen so far)
    key on the activityId alone; a multi-leg transaction adds the leg index."""
    aid = txn.get("activityId")
    if aid is None:
        raise AdapterError("transaction without an activityId")
    return str(aid) if len(_legs(txn)) <= 1 else f"{aid}:{leg_index}"


def symbol_of(txn: dict) -> str | None:
    """The underlying / ticker a transaction trades (for exclusion lists)."""
    for leg in _legs(txn):
        ins = leg.get("instrument") or {}
        if ins.get("assetType") == "OPTION":
            return (ins.get("underlyingSymbol") or "").lstrip("$").upper() or None
        if ins.get("assetType") == "FUTURE":
            return (ins.get("symbol") or "").split(":")[0].upper() or None
        return (ins.get("symbol") or "").upper() or None
    return None


def instrument_of(txn: dict) -> str | None:
    for leg in _legs(txn):
        a = (leg.get("instrument") or {}).get("assetType")
        return {"EQUITY": "STOCK", "COLLECTIVE_INVESTMENT": "STOCK", "OPTION": "OPTION",
                "FUTURE": "FUTURE"}.get(a)
    return None


def _option_fields(ins: dict) -> dict:
    sym = (ins.get("symbol") or "").upper()
    m = _OCC.match(sym.replace(" ", " "))
    if m:
        root = m.group("root")
        expiry = f"20{m.group('yy')}-{m.group('mm')}-{m.group('dd')}"
        strike = int(m.group("strike")) / 1000.0
        opt_type = "CALL" if m.group("cp") == "C" else "PUT"
    else:
        root = (ins.get("underlyingSymbol") or "").lstrip("$").upper()
        exp = ins.get("expirationDate")
        if not (root and exp and ins.get("strikePrice") is not None and ins.get("putCall")):
            raise AdapterError(f"option without contract details: {sym!r}")
        expiry = _parse_time(exp).astimezone(ET).date().isoformat()
        strike = float(ins["strikePrice"])
        opt_type = str(ins["putCall"]).upper()
    root = OPTION_ROOTS.get(root, root)
    return {"ticker": root, "option_expiry": expiry, "option_strike": strike, "option_type": opt_type}


def _leg_fill(txn: dict, leg: dict, leg_index: int, when: datetime) -> dict:
    ins = leg.get("instrument") or {}
    asset = ins.get("assetType")
    qty_signed = float(leg.get("amount") or 0.0)
    if not qty_signed:
        raise AdapterError(f"zero quantity fill in transaction {txn.get('activityId')}")
    qty = abs(qty_signed)
    qty = int(qty) if qty == int(qty) else qty
    action = "BOT" if qty_signed > 0 else "SOLD"
    base = {"action": action, "qty": qty, "price": _price(leg.get("price")),
            "amount": round(float(leg.get("cost") or 0.0), 2),
            "option_expiry": None, "option_strike": None, "option_type": None}
    if asset == "OPTION":
        base.update(_option_fields(ins), instrument_type="OPTION")
        day = when.astimezone(ET).date()
    elif asset == "FUTURE":
        base.update(ticker=(ins.get("symbol") or "").split(":")[0].upper(), instrument_type="FUTURE")
        td = txn.get("tradeDate")
        day = (_parse_time(td).astimezone(ET).date() if td else when.astimezone(ET).date())
    elif asset in ("EQUITY", "COLLECTIVE_INVESTMENT"):
        base.update(ticker=(ins.get("symbol") or "").upper(), instrument_type="STOCK")
        day = when.astimezone(ET).date()
    else:
        raise AdapterError(f"unsupported asset type {asset!r}")
    if not base.get("ticker"):
        raise AdapterError(f"fill without a symbol in transaction {txn.get('activityId')}")
    base.update({
        "date": _raw(day), "iso_date": day.isoformat(),
        "time": when.astimezone(ET).strftime("%H:%M:%S"),
        "commission": 0.0,
        "_fill_keys": [fill_key(txn, leg_index)],
        "_order_id": str(txn.get("orderId") or ""),
        "_time_utc": when.astimezone(timezone.utc).isoformat(),
    })
    return base


def _vertical(a: dict, b: dict) -> dict | None:
    """Two single-option fills of one order and second -> one vertical fill, or None."""
    if not (a["instrument_type"] == b["instrument_type"] == "OPTION"):
        return None
    if (a["ticker"], a["option_expiry"], a["option_type"], a["qty"]) != \
            (b["ticker"], b["option_expiry"], b["option_type"], b["qty"]):
        return None
    if a["action"] == b["action"] or a["option_strike"] == b["option_strike"]:
        return None
    amount = round(a["amount"] + b["amount"], 2)
    action = "SOLD" if amount > 0 else "BOT"
    first, second = (a, b) if a["action"] == action else (b, a)
    net = round(abs(first["price"] - second["price"]), 5)
    fill = dict(first)
    fill.update({
        "action": action, "price": net, "amount": amount,
        "option_strike": first["option_strike"], "option_strike2": second["option_strike"],
        "commission": round(a["commission"] + b["commission"], 2),
        "_fill_keys": first["_fill_keys"] + second["_fill_keys"],
    })
    return fill


def transactions_to_executions(transactions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (execution dicts in statement order, problems). Skips non-TRADE and non-VALID
    transactions. Problems are transactions it refused, each {activityId, reason}."""
    problems: list[dict] = []
    rows: list[tuple] = []  # (time, order_id, activityId, fills)
    for txn in transactions:
        if txn.get("type") != "TRADE":
            continue
        if txn.get("status") not in (None, "VALID"):
            continue
        try:
            when = _parse_time(txn.get("time"))
            legs = _legs(txn)
            if not legs:
                raise AdapterError("trade without a security leg")
            fills = [_leg_fill(txn, leg, i, when) for i, leg in enumerate(legs)]
            fee = _fees(txn)
            fills[0]["commission"] = fee
        except AdapterError as exc:
            problems.append({"activityId": txn.get("activityId"), "reason": str(exc)})
            continue
        rows.append((when, str(txn.get("orderId") or ""), int(txn.get("activityId") or 0), fills))

    rows.sort(key=lambda r: (r[0], r[2]))

    # Verticals: exactly two option fills of one order in one second.
    by_order_second: dict[tuple, list[int]] = defaultdict(list)
    for idx, (when, order_id, _aid, fills) in enumerate(rows):
        if order_id and len(fills) == 1 and fills[0]["instrument_type"] == "OPTION":
            by_order_second[(order_id, when.replace(microsecond=0))].append(idx)
    merged: dict[int, dict] = {}
    dropped: set[int] = set()
    for idxs in by_order_second.values():
        if len(idxs) != 2:
            continue
        v = _vertical(rows[idxs[0]][3][0], rows[idxs[1]][3][0])
        if v:
            merged[idxs[0]] = v
            dropped.add(idxs[1])

    out: list[dict] = []
    for idx, (_when, _oid, _aid, fills) in enumerate(rows):
        if idx in dropped:
            continue
        if idx in merged:
            out.append(merged[idx])
        else:
            out.extend(fills)
    return out, problems
