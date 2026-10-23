"""Read-only, all-history descriptive rule results."""
from .models import ENGINE_VERSION, NY
from .simulate import simulate
from .assignments import applies


def compare(entries, rules, only_ids=None):
    selected = [e for e in entries if e.get("trade") and
                (only_ids is None or e["id"] in only_ids)]
    results = []
    for rule in rules:
        trades = []
        for entry in selected:
            if not applies(rule, entry.get("journal_trade", {})):
                continue
            trade = entry["trade"]
            result = simulate(trade, entry["bars"], rule, spy_bars=entry.get("spy_bars"))
            result.update(trade_id=trade.id, session=trade.entered_at.astimezone(NY).date().isoformat())
            trades.append(result)
        results.append({"rule": rule.model_dump(), "rule_hash": rule.rule_hash,
                        "applies_to_setups": rule._applies_to_setups,
                        "eligible": sum(t["eligible"] for t in trades),
                        "excluded": sum(not t["eligible"] for t in trades), "trades": trades})
    return {"engine": ENGINE_VERSION, "rules": results}
