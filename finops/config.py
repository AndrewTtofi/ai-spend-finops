"""`config/finops.yaml`: who owns which spend, what each team may spend, what was
committed to each vendor, and what a seat costs. Everything a report needs that is a
decision rather than a measurement.

Validated when it is loaded, so a typo in a team name fails here and not as a silently
empty line in a report.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

VENDORS = ("anthropic", "openai")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Vendor(Strict):
    commitment_usd_per_month: float = Field(ge=0)  # what was committed, used or not


class Tier(Strict):
    soft_usd: float = Field(ge=0)  # warn past this
    hard_usd: float = Field(ge=0)  # the cap

    @model_validator(mode="after")
    def _soft_below_hard(self) -> Tier:
        if self.soft_usd > self.hard_usd:
            raise ValueError("soft_usd must not be above hard_usd")
        return self


class Team(Strict):
    tier: str


class Plan(Strict):
    usd_per_seat_month: float = Field(gt=0)
    okta_group_id: str  # membership of this group is what holds the seat
    activity: str  # the product whose use counts as using the seat


class Seats(Strict):
    plans: dict[str, Plan] = {}
    inactive_days: int = Field(default=45, ge=1)  # unused this long: a reclaim candidate
    grace_days: int = Field(default=14, ge=0)  # a new seat is not judged before this


class Anomalies(Strict):
    baseline_days: int = Field(default=14, ge=3)
    threshold: float = Field(default=3.5, gt=0)  # robust z-score
    min_usd: float = Field(default=25, ge=0)  # ignore jumps smaller than this


class Config(Strict):
    vendors: dict[str, Vendor]
    # vendor → the vendor's scope id (workspace, project) → the team that owns it.
    owners: dict[str, dict[str, str]] = {}
    tiers: dict[str, Tier]
    teams: dict[str, Team]
    seats: Seats = Seats()
    anomalies: Anomalies = Anomalies()

    @model_validator(mode="after")
    def _consistent(self) -> Config:
        for vendor in [*self.vendors, *self.owners]:
            if vendor not in VENDORS:
                raise ValueError(f"unknown vendor {vendor!r}; known: {', '.join(VENDORS)}")
        for name, team in self.teams.items():
            if team.tier not in self.tiers:
                raise ValueError(f"teams.{name}: unknown tier {team.tier!r}")
        for vendor, scopes in self.owners.items():
            for scope, owner in scopes.items():
                if owner not in self.teams:
                    raise ValueError(f"owners.{vendor}.{scope}: unknown team {owner!r}")
        return self

    def owner(self, vendor: str, scope: str) -> str | None:
        """The team that owns a vendor scope, or None: spend nobody has claimed."""
        return self.owners.get(vendor, {}).get(scope)


def load(path: str | Path) -> Config:
    return Config.model_validate(yaml.safe_load(Path(path).read_text()))
