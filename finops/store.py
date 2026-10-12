"""A local copy of what was fetched, in one SQLite file.

Reports read from here, not from the vendors: a report can be re-run and gives the same
answer, a month can be reconciled after the vendor's own retention has moved on, and
syncing is incremental. Writes are upserts, so syncing the same days twice changes
nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from finops.model import Activity, Cost, Seat

SCHEMA = """
CREATE TABLE IF NOT EXISTS costs (
    day TEXT NOT NULL, vendor TEXT NOT NULL, scope TEXT NOT NULL, item TEXT NOT NULL,
    usd REAL NOT NULL, PRIMARY KEY (day, vendor, scope, item)
);
CREATE TABLE IF NOT EXISTS activity (
    day TEXT NOT NULL, email TEXT NOT NULL, product TEXT NOT NULL,
    PRIMARY KEY (day, email, product)
);
CREATE TABLE IF NOT EXISTS seats (
    email TEXT NOT NULL, plan TEXT NOT NULL, name TEXT NOT NULL,
    first_seen TEXT NOT NULL,   -- the first sync that found this person holding the seat
    last_seen TEXT NOT NULL,    -- the latest one
    PRIMARY KEY (email, plan)
);
CREATE TABLE IF NOT EXISTS alerts (key TEXT PRIMARY KEY, sent_on TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: str | Path = "data/finops.sqlite3") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- writing -----------------------------------------------------------------------

    def save_costs(self, costs: Iterable[Cost]) -> int:
        rows = [(c.day.isoformat(), c.vendor, c.scope, c.item, c.usd) for c in costs]
        with self.db:
            self.db.executemany(
                "INSERT INTO costs VALUES (?,?,?,?,?) ON CONFLICT(day, vendor, scope, item) "
                "DO UPDATE SET usd = excluded.usd",  # a vendor may restate a day: latest wins
                rows,
            )
        return len(rows)

    def save_activity(self, activity: Iterable[Activity]) -> int:
        rows = [(a.day.isoformat(), a.email.lower(), a.product) for a in activity]
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO activity VALUES (?,?,?)", rows)
        return len(rows)

    def save_seats(self, plan: str, seats: Iterable[Seat], today: date) -> int:
        """Replace the roster of one plan with what the identity provider says today.
        Someone no longer in it has given the seat up; someone new starts their grace
        period now."""
        seats = list(seats)
        day = today.isoformat()
        with self.db:
            for seat in seats:
                self.db.execute(
                    "INSERT INTO seats VALUES (?,?,?,?,?) ON CONFLICT(email, plan) "
                    "DO UPDATE SET name = excluded.name, last_seen = excluded.last_seen",
                    (seat.email.lower(), plan, seat.name, day, day),
                )
            self.db.execute("DELETE FROM seats WHERE plan = ? AND last_seen != ?", (plan, day))
        return len(seats)

    def alert_once(self, key: str, today: date) -> bool:
        """True the first time a key is seen: an alert is sent once, not on every run."""
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO alerts VALUES (?,?)", (key, today.isoformat())
            )
        return cur.rowcount == 1

    # --- reading -----------------------------------------------------------------------

    def costs(self, start: date, end: date) -> list[Cost]:
        """Costs for days in [start, end]."""
        rows = self.db.execute(
            "SELECT day, vendor, scope, item, usd FROM costs WHERE day >= ? AND day <= ? "
            "ORDER BY day, vendor, scope, item",
            (start.isoformat(), end.isoformat()),
        )
        return [Cost(date.fromisoformat(d), v, s, i, u) for d, v, s, i, u in rows]

    def last_active(self, product: str) -> dict[str, date]:
        rows = self.db.execute(
            "SELECT email, max(day) FROM activity WHERE product = ? GROUP BY email", (product,)
        )
        return {email: date.fromisoformat(day) for email, day in rows}

    def seats(self, plan: str) -> list[tuple[Seat, date]]:
        """The current roster of a plan, each with the day it was first seen."""
        rows = self.db.execute(
            "SELECT email, name, first_seen FROM seats WHERE plan = ? ORDER BY email", (plan,)
        )
        return [(Seat(e, n, plan), date.fromisoformat(f)) for e, n, f in rows]
