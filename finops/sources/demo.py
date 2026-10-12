"""Sample data, so every report can be shown without a single credential.

Deterministic (a fixed seed), and built to contain the things the reports exist to find:
a team past its soft cap, one day of runaway spend, spend nobody has claimed, a vendor
commitment that is not being used, and seats nobody uses. The figures are invented.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from finops.model import Activity, Cost, Seat

# vendor, scope, item, typical dollars a day
LINES = [
    ("anthropic", "wrkspc_eng", "claude-sonnet", 95.0),
    ("anthropic", "wrkspc_eng", "claude-opus", 60.0),
    ("anthropic", "wrkspc_compliance", "claude-sonnet", 22.0),
    ("anthropic", "default", "claude-haiku", 9.0),  # the default workspace: nobody's
    ("openai", "proj_trading", "gpt, input", 70.0),
    ("openai", "proj_trading", "gpt, output", 45.0),
    ("openai", "proj_research", "embeddings", 12.0),
    ("openai", "proj_sandbox", "gpt, input", 6.0),  # a project created outside the process
]
RUNAWAY = ("openai", "proj_research", 9)  # a batch job in a loop, this many days before the end
PEOPLE = 40
IDLE = (3, 9, 14, 17, 22, 28, 33)  # seat holders who never use it
LAPSED = (5, 19)  # used it once, long ago


def costs(end: date, days: int = 75) -> list[Cost]:
    rng = random.Random(7)
    out = []
    for offset in range(days, -1, -1):
        day = end - timedelta(days=offset)
        weekend = 0.35 if day.weekday() >= 5 else 1.0
        for vendor, scope, item, typical in LINES:
            usd = typical * weekend * rng.uniform(0.8, 1.2)
            if (vendor, scope, offset) == RUNAWAY:
                usd *= 14
            out.append(Cost(day, vendor, scope, item, round(usd, 2)))
    return out


def seats(plan: str = "claude-team") -> list[Seat]:
    return [Seat(f"user{n:02d}@example.com", f"User {n:02d}", plan) for n in range(1, PEOPLE + 1)]


def activity(end: date, days: int = 75, product: str = "claude_code") -> list[Activity]:
    rng = random.Random(11)
    out = []
    for n in range(1, PEOPLE + 1):
        email = f"user{n:02d}@example.com"
        if n in IDLE:
            continue
        if n in LAPSED:
            out.append(Activity(end - timedelta(days=62), email, product))
            continue
        for offset in range(days, -1, -1):
            day = end - timedelta(days=offset)
            if day.weekday() < 5 and rng.random() < 0.7:
                out.append(Activity(day, email, product))
    return out
