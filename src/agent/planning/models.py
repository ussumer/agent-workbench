"""Versioned planning contracts. Money stays in decimal strings or integer cents."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
Money = Annotated[str, StringConstraints(strict=True, pattern=r"^(0|[1-9][0-9]*)\.[0-9]{2}$")]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonnegativeInt = Annotated[StrictInt, Field(ge=0)]


def cents(value: str) -> int:
    whole, fraction = value.split(".")
    return int(whole) * 100 + int(fraction)


def money(value: int) -> str:
    return f"{value // 100}.{value % 100:02d}"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Demand(Contract):
    part_id: Identifier
    quantity: PositiveInt
    max_lead_days: NonnegativeInt
    required: StrictBool
    priority: NonnegativeInt = 0
    allow_partial: StrictBool = False
    allow_supplier_split: StrictBool = False

    @model_validator(mode="after")
    def required_is_full(self) -> Demand:
        if self.required and self.allow_partial:
            raise ValueError("required demand cannot allow partial fulfillment")
        return self


class Offer(Contract):
    offer_id: Identifier
    supplier_id: Identifier
    part_id: Identifier
    supplier_active: StrictBool
    part_active: StrictBool
    relationship_active: StrictBool
    unit_price: Money | None
    lead_days: NonnegativeInt | None
    source_ref: Identifier
    source_status: Literal["verified", "missing", "conflict"]

    @model_validator(mode="after")
    def evidence_fields(self) -> Offer:
        if self.unit_price is not None and cents(self.unit_price) == 0:
            raise ValueError("offer price must be positive")
        incomplete = self.unit_price is None or self.lead_days is None
        if self.source_status == "verified" and incomplete:
            raise ValueError("verified offer needs price and lead time")
        if self.source_status == "missing" and not incomplete:
            raise ValueError("missing offer must identify an unknown price or lead time")
        return self


class CommittedLine(Contract):
    """Control-plane evidence of an existing order, never an Actor-selected quote."""
    order_id: Identifier
    offer_id: Identifier
    supplier_id: Identifier
    part_id: Identifier
    quantity: PositiveInt
    unit_price: Money
    lead_days: NonnegativeInt
    source_ref: Identifier

    @model_validator(mode="after")
    def positive_price(self) -> CommittedLine:
        if cents(self.unit_price) <= 0:
            raise ValueError("committed price must be positive")
        return self


class PlanningProblem(Contract):
    schema_version: Literal[1, 2] = 1
    goal_id: Identifier
    revision: PositiveInt = 1
    currency: Literal["CNY"] = "CNY"
    budget: Money
    demands: tuple[Demand, ...]
    offers: tuple[Offer, ...]
    commitments: tuple[CommittedLine, ...] = ()

    @model_validator(mode="after")
    def unique_keys(self) -> PlanningProblem:
        if not self.demands:
            raise ValueError("at least one demand is required")
        if len({d.part_id for d in self.demands}) != len(self.demands):
            raise ValueError("duplicate demand part_id")
        if len({o.offer_id for o in self.offers}) != len(self.offers):
            raise ValueError("duplicate offer_id")
        # Multiple observations for one relationship must be resolved to a single
        # conflict Offer, rather than allowing the Actor to cherry-pick a source.
        if len({(o.part_id, o.supplier_id) for o in self.offers}) != len(self.offers):
            raise ValueError("duplicate supplier/part evidence; consolidate as conflict")
        if any(o.part_id not in {d.part_id for d in self.demands} for o in self.offers):
            raise ValueError("offer has no corresponding demand")
        if len({(c.order_id, c.part_id) for c in self.commitments}) != len(self.commitments):
            raise ValueError("duplicate committed order line")
        if self.commitments and self.schema_version != 2:
            raise ValueError("committed problems require schema_version 2")
        return self


class PlanLine(Contract):
    offer_id: Identifier
    quantity: PositiveInt


class Plan(Contract):
    goal_id: Identifier
    revision: PositiveInt
    lines: tuple[PlanLine, ...]

    @model_validator(mode="after")
    def unique_lines(self) -> Plan:
        if len({line.offer_id for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate plan offer_id")
        return self
