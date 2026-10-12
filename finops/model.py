"""The three kinds of record everything else works on. Sources produce them, the store
keeps them, the reports read them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Cost:
    """What a vendor billed for one day, for one scope and one line item."""

    day: date
    vendor: str  # anthropic | openai
    scope: str  # the vendor's own grouping: a workspace or project id ("default" if none)
    item: str  # what it was for: a model or a line item
    usd: float


@dataclass(frozen=True)
class Activity:
    """Someone used a product on a day. Only the fact, never what they did."""

    day: date
    email: str
    product: str  # e.g. claude_code


@dataclass(frozen=True)
class Seat:
    """Someone holds a paid seat, according to the identity provider."""

    email: str
    name: str
    plan: str
