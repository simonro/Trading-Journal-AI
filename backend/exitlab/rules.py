from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from .models import digest


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class Tranche(StrictModel):
    fraction: float = Field(gt=0, le=1)
    kind: Literal["target", "vwap_touch", "minutes", "clock", "close"]
    r: float | None = Field(default=None, gt=0)
    minutes: int | None = Field(default=None, gt=0, le=390)
    clock: str | None = Field(default=None, pattern=r"^(?:09:[3-5][0-9]|1[0-5]:[0-5][0-9])$")

    @model_validator(mode="after")
    def required_value(self):
        expected = {"target": "r", "minutes": "minutes", "clock": "clock", "close": None, "vwap_touch": None}[self.kind]
        for name in ("r", "minutes", "clock"):
            if (getattr(self, name) is not None) != (name == expected):
                raise ValueError("Supply only the value belonging to the tranche kind")
        return self


class RStep(StrictModel):
    at_r: float = Field(gt=0)
    stop_r: float

    @model_validator(mode="after")
    def protective(self):
        if not -1 <= self.stop_r < self.at_r:
            raise ValueError("R-step stop must be below its activation and no lower than initial risk")
        return self


class ATRTrail(StrictModel):
    length: int = Field(default=14, ge=2, le=200)
    multiple: float = Field(default=2, gt=0, le=100)


class Indicator(StrictModel):
    kind: Literal["vwap", "ema9", "ema20", "ema_cross", "rs_rw"]
    timeframe: Literal[1, 5] = 1
    confirmation: int = Field(default=1, ge=1, le=20)

    @model_validator(mode="after")
    def five_minute_rs(self):
        if self.kind == "rs_rw" and self.timeframe != 5:
            raise ValueError("RS/RW always uses completed 5-minute bars")
        if self.kind != "rs_rw" and self.confirmation != 1:
            raise ValueError("Confirmation is available only for RS/RW")
        return self


class Rule(StrictModel):
    # Stored outside immutable policy JSON/hash, including for presets.
    _applies_to_setups: tuple[dict[str, str], ...] | None = PrivateAttr(default=None)
    _saved_id: int | None = PrivateAttr(default=None)
    name: str = Field(min_length=1, max_length=100)
    tranches: tuple[Tranche, ...] = Field(default=(), max_length=3)
    r_steps: tuple[RStep, ...] = Field(default=(), max_length=20)
    atr: ATRTrail | None = None
    indicator: Indicator | None = None
    tick_size: float = Field(default=0.01, gt=0, le=1)

    @model_validator(mode="after")
    def coherent(self):
        if self.tranches and abs(sum(t.fraction for t in self.tranches) - 1) > 1e-9:
            raise ValueError("Tranche fractions must sum to one; use a close tranche for the remainder")
        steps = list(self.r_steps)
        if any(b.at_r <= a.at_r or b.stop_r < a.stop_r for a, b in zip(steps, steps[1:])):
            raise ValueError("R steps must have increasing activations and stops that never loosen")
        return self

    @property
    def rule_hash(self):
        policy = self.model_dump(exclude={"name"})
        # Preserve all pre-existing hashes after adding the RS-only parameter.
        if policy.get("indicator") and policy["indicator"]["kind"] != "rs_rw":
            policy["indicator"].pop("confirmation", None)
        return digest(policy)


class Costs(StrictModel):
    slippage_bps: float = Field(default=0, ge=0, le=100)
    per_fill: float = Field(default=0, ge=0)
    per_share: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def zero_modeled_costs(self):
        # Accept legacy settings/request shapes, but never apply their values.
        for name in ("slippage_bps", "per_fill", "per_share"):
            object.__setattr__(self, name, 0.0)
        return self

    @property
    def label(self):
        return "Zero modeled costs and slippage"


PRESETS = (
    Rule(name="1R target", tranches=(Tranche(kind="target", r=1, fraction=1),)),
    Rule(name="2R target", tranches=(Tranche(kind="target", r=2, fraction=1),)),
    Rule(name="Hold to close"),
    Rule(name="Breakeven at +1R", r_steps=(RStep(at_r=1, stop_r=0),)),
)
