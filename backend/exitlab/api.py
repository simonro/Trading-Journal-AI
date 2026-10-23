"""Local JSON API; calculation and persistence live in the package services."""
from datetime import timedelta
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator, model_validator
from .rules import Rule, Costs, StrictModel
from . import service, simple
from .bars import fetch
from .models import timestamp
from .stops import save_stop
from .eligibility import from_row
from .excursion import excursions, persist

router = APIRouter(prefix="/api/exitlab", tags=["exitlab"])


def connection():
    # Resolve lazily so tests and application use the current database module.
    from database import get_db
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


class RecomputeRequest(StrictModel):
    account_id: int = Field(gt=0)
    feed: Literal["sip", "iex"] = "sip"


class BarsRequest(StrictModel):
    symbol: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9.\-]{0,14}$")
    start: str
    end: str
    feed: Literal["sip", "iex"] = "sip"
    refresh: bool = False
    include_warmup: bool = False


class StopRequest(StrictModel):
    price: float = Field(gt=0)


class SetupSelection(StrictModel):
    group: Literal["strategy", "auto"]
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def named_setup(cls, value):
        if not value.strip():
            raise ValueError("Choose a named setup")
        return value


class AssignmentFields(StrictModel):
    applies_to_setups: tuple[SetupSelection, ...] | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("applies_to_setups")
    @classmethod
    def clean_setups(cls, value):
        if value is not None:
            unique = {(item.group, item.name): item for item in value}
            return tuple(unique[key] for key in sorted(unique))
        return None

class RuleCreate(Rule, AssignmentFields):
    pass


@router.get("/setups")
def setups(account_id: int = Query(gt=0), conn=Depends(connection)):
    from .assignments import setup_options
    return setup_options(conn, account_id)


@router.get("/rules")
def list_rules(conn=Depends(connection)):
    import json
    return [{**dict(r), "json": json.loads(r["json"]), "applies_to_setups": json.loads(r["applies_to_setups"])} for r in conn.execute("SELECT * FROM exit_rules ORDER BY id")]


@router.post("/rules", status_code=201)
def create_rule(body: RuleCreate, conn=Depends(connection)):
    policy = Rule.model_validate(body.model_dump(exclude={"applies_to_setups"}))
    with conn:
        cursor = conn.execute("INSERT INTO exit_rules(name,json,hash,is_preset,applies_to_setups) VALUES(?,?,?,0,?)",
                              (policy.name, policy.model_dump_json(), policy.rule_hash, json.dumps(body.model_dump(mode="json")["applies_to_setups"])))
    return {"id": cursor.lastrowid, "hash": policy.rule_hash, "rule": policy.model_dump(),
            "show_in_tables": True, "applies_to_setups": body.applies_to_setups}


class RuleVisibility(AssignmentFields):
    show_in_tables: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or ("show_in_tables" in self.model_fields_set and self.show_in_tables is None):
            raise ValueError("Supply an assignment or a visibility preference")
        return self


@router.patch("/rules/{rule_id}")
def rule_visibility(rule_id: int, body: RuleVisibility, conn=Depends(connection)):
    with conn:
        row = conn.execute("SELECT * FROM exit_rules WHERE id=?", (rule_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "unknown_rule_id")
        if "show_in_tables" in body.model_fields_set:
            if row['is_preset']:
                raise HTTPException(422, "Preset rules always appear in tables.")
            conn.execute("UPDATE exit_rules SET show_in_tables=? WHERE id=?", (int(body.show_in_tables), rule_id))
        if "applies_to_setups" in body.model_fields_set:
            conn.execute("UPDATE exit_rules SET applies_to_setups=? WHERE id=?", (json.dumps(body.model_dump(mode="json")["applies_to_setups"]), rule_id))
        updated = conn.execute("SELECT * FROM exit_rules WHERE id=?", (rule_id,)).fetchone()
    return {"id": rule_id, "show_in_tables": bool(updated['show_in_tables']),
            "applies_to_setups": json.loads(updated['applies_to_setups'])}


@router.get("/readiness")
def readiness(account_id: int = Query(gt=0), feed: Literal["sip", "iex"] = "sip", conn=Depends(connection)):
    return service.readiness(conn, account_id, feed)


@router.post("/bars/fetch")
def fetch_bars(body: BarsRequest, conn=Depends(connection)):
    start, end = timestamp(body.start), timestamp(body.end)
    if end-start > timedelta(days=7) or start.second or start.microsecond or end.second or end.microsecond:
        raise ValueError("Use aligned minute boundaries and windows no longer than seven days")
    result = fetch(conn, body.symbol, start, end, body.feed, body.refresh)
    if body.symbol.upper() != "SPY":
        result["benchmark"] = fetch(conn, "SPY", start, end, body.feed, body.refresh)
    if body.include_warmup:
        from .calendar import previous_session
        from .models import NY
        prior_start, prior_end = previous_session(start.astimezone(NY).date().isoformat())
        result["warmup"] = fetch(conn, body.symbol, prior_start, prior_end, body.feed, body.refresh)
        if body.symbol.upper() != "SPY":
            result["warmup_benchmark"] = fetch(conn, "SPY", prior_start, prior_end, body.feed, body.refresh)
    return result


@router.post("/trades/{trade_id}/stops", status_code=201)
def stop(trade_id: int, body: StopRequest, conn=Depends(connection)):
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="trade_not_found")
    try:
        parsed = from_row(row, body.price)
    except (ValueError, TypeError, KeyError):
        parsed = None  # The journal stop is editable for structurally excluded trades too.
    if parsed and parsed.direction * (parsed.entry - body.price) < 0.02 - 1e-9:
        raise HTTPException(422, "Enter a stop on the loss side, at least two cents from entry.")
    save_stop(conn, row, body.price)
    return {"trade_id": trade_id, "stop_loss": body.price}


@router.post("/excursions/recompute")
def recompute(body: RecomputeRequest, conn=Depends(connection)):
    results = []
    with conn:
        for entry in service.load_entries(conn, body.account_id, body.feed):
            if entry.get("trade"):
                result = excursions(entry["trade"], entry["bars"])
                persist(conn, entry["trade"], result)
                results.append({"trade_id": entry["id"], **result})
            else:
                results.append({"trade_id": entry["id"], "eligible": False, "reasons": entry["reasons"]})
    return results


class HistoryRequest(StrictModel):
    account_id: int = Field(gt=0)


class JournalCosts(StrictModel):
    slippage_bps: float = Field(default=0, ge=0, le=100)
    per_share: float = Field(default=0, ge=0)


@router.get('/settings')
def get_settings(conn=Depends(connection)):
    return simple.settings(conn).model_dump()


@router.put('/settings')
def put_settings(body: JournalCosts, conn=Depends(connection)):
    return simple.save_settings(conn, Costs(**body.model_dump()))


@router.post('/history')
def history(body: HistoryRequest, conn=Depends(connection)):
    return simple.history(conn, body.account_id)[0]


@router.get('/trades/{trade_id}/simple')
def simple_replay(trade_id: int, conn=Depends(connection)):
    return simple.simple_replay(conn, trade_id)
