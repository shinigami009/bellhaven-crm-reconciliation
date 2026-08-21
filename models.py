from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Facility:
    name: str
    street: str
    city: str
    state: str
    zip: str
    care_offerings: tuple[str, ...]
    source_url: str
    scraped_at: str
    raw_address: str
    normalized_name: str = ""
    normalized_street: str = ""
    phone: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["care_offerings"] = list(self.care_offerings)
        return value


@dataclass(frozen=True)
class Account:
    account_id: str
    name: str
    parent_id: str
    parent_name: str
    billing_street: str
    billing_city: str
    billing_state: str
    billing_zip: str
    care_type: str
    status: str
    phone: str
    lifetime_revenue: int
    outstanding_ar: int
    chow_current_account: str
    duplicate_of_account: str
    note: str
    created_by_candidate: bool
    updated_at: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Account":
        missing = [name for name in cls.__dataclass_fields__ if name not in value]
        if missing:
            raise ValueError(f"Account response missing fields: {', '.join(missing)}")
        return cls(**{name: value[name] for name in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Proposal:
    proposal_id: str
    proposal_type: str
    target_account_id: str | None
    action: str
    proposed_values: dict[str, Any]
    expected_values: dict[str, Any]
    evidence: dict[str, Any]
    note: str
    created_at: str
    decision: str = "PENDING"
    decision_at: str | None = None
    api_result: dict[str, Any] | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
