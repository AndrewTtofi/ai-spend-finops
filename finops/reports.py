"""The four reports. Each is a pure function of records and config: the same inputs give
the same report, which is what makes them testable and re-runnable.

    reconcile   the month against what was committed to each vendor
    caps        each team's spend against its tier's soft and hard caps
    anomalies   a day's spend that is far outside that team's own recent days
    seats       paid seats nobody uses, and what reclaiming them saves
"""

from __future__ import annotations

import calendar
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from finops.config import Config
from finops.model import Cost, Seat

UNALLOCATED = "(unallocated)"


def month_bounds(month: str) -> tuple[date, date]:
    """'2026-09' → (2026-09-01, 2026-09-30)."""
    year, mon = (int(part) for part in month.split("-"))
    return date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])


def by_team(costs: list[Cost], config: Config) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for cost in costs:
        totals[config.owner(cost.vendor, cost.scope) or UNALLOCATED] += cost.usd
    return dict(totals)


# --- reconcile -------------------------------------------------------------------------


@dataclass(frozen=True)
class VendorLine:
    vendor: str
    commitment_usd: float
    actual_usd: float  # billed so far this month
    projected_usd: float  # at the month's pace so far; equals actual once the month is over
    days_elapsed: int
    days_in_month: int

    @property
    def variance_usd(self) -> float:
        """Projected minus committed. Negative: commitment that will go unused.
        Positive: overage."""
        return self.projected_usd - self.commitment_usd

    @property
    def used_share(self) -> float:
        return self.projected_usd / self.commitment_usd if self.commitment_usd else 0.0


@dataclass(frozen=True)
class Reconciliation:
    month: str
    as_of: date
    vendors: list[VendorLine]
    teams: dict[str, float]  # team → billed this month, with "(unallocated)"
    unallocated: dict[str, float]  # "vendor/scope" → billed, for spend nobody has claimed


def reconcile(costs: list[Cost], config: Config, month: str, today: date) -> Reconciliation:
    """`costs` may cover more than the month; only the month's days are counted."""
    start, end = month_bounds(month)
    as_of = min(today, end)
    in_month = [c for c in costs if start <= c.day <= as_of]
    elapsed, total_days = (as_of - start).days + 1, (end - start).days + 1
    lines = []
    for vendor, terms in sorted(config.vendors.items()):
        actual = sum(c.usd for c in in_month if c.vendor == vendor)
        lines.append(
            VendorLine(
                vendor=vendor,
                commitment_usd=terms.commitment_usd_per_month,
                actual_usd=actual,
                projected_usd=actual / elapsed * total_days if elapsed > 0 else 0.0,
                days_elapsed=max(elapsed, 0),
                days_in_month=total_days,
            )
        )
    unclaimed: dict[str, float] = defaultdict(float)
    for cost in in_month:
        if config.owner(cost.vendor, cost.scope) is None:
            unclaimed[f"{cost.vendor}/{cost.scope}"] += cost.usd
    return Reconciliation(month, as_of, lines, by_team(in_month, config), dict(unclaimed))


# --- caps ------------------------------------------------------------------------------


@dataclass(frozen=True)
class CapLine:
    team: str
    tier: str
    spent_usd: float
    soft_usd: float
    hard_usd: float

    @property
    def status(self) -> str:
        if self.spent_usd >= self.hard_usd:
            return "over hard cap"
        return "over soft cap" if self.spent_usd >= self.soft_usd else "ok"

    @property
    def used_share(self) -> float:
        return self.spent_usd / self.hard_usd if self.hard_usd else 0.0


def caps(costs: list[Cost], config: Config, month: str, today: date) -> list[CapLine]:
    start, end = month_bounds(month)
    spent = by_team([c for c in costs if start <= c.day <= min(today, end)], config)
    lines = []
    for name, team in config.teams.items():
        tier = config.tiers[team.tier]
        lines.append(CapLine(name, team.tier, spent.get(name, 0.0), tier.soft_usd, tier.hard_usd))
    return sorted(lines, key=lambda line: -line.used_share)


# --- anomalies -------------------------------------------------------------------------


@dataclass(frozen=True)
class Anomaly:
    day: date
    vendor: str
    team: str
    usd: float
    typical_usd: float  # the median of the baseline days
    score: float  # how many (robust) standard deviations above typical

    @property
    def key(self) -> str:
        return f"anomaly:{self.day.isoformat()}:{self.vendor}:{self.team}"


def anomalies(costs: list[Cost], config: Config, day: date) -> list[Anomaly]:
    """Spend on `day` far above the same team's own recent days, per vendor.

    The baseline is the median of the days before, and spread is the median absolute
    deviation: one earlier spike doesn't raise the bar the way a mean and a standard
    deviation would. A jump also has to be worth at least `min_usd`, so a team that
    normally spends two dollars and spends ten is not an alert.
    """
    settings = config.anomalies
    first = day - timedelta(days=settings.baseline_days)
    daily: dict[tuple[str, str], dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for cost in costs:
        if first <= cost.day <= day:
            team = config.owner(cost.vendor, cost.scope) or UNALLOCATED
            daily[(cost.vendor, team)][cost.day] += cost.usd
    found = []
    for (vendor, team), per_day in daily.items():
        baseline = [
            per_day.get(first + timedelta(days=n), 0.0) for n in range(settings.baseline_days)
        ]
        today_usd = per_day.get(day, 0.0)
        typical = statistics.median(baseline)
        spread = statistics.median(abs(x - typical) for x in baseline)
        if today_usd - typical < settings.min_usd:
            continue
        # 0.6745 scales the deviation so the score reads like a standard z-score. With no
        # spread at all (a perfectly flat baseline), any jump past min_usd is anomalous.
        score = 0.6745 * (today_usd - typical) / spread if spread > 0 else float("inf")
        if score >= settings.threshold:
            found.append(Anomaly(day, vendor, team, today_usd, typical, score))
    return sorted(found, key=lambda a: -a.usd)


# --- seats -----------------------------------------------------------------------------


@dataclass(frozen=True)
class IdleSeat:
    email: str
    name: str
    last_active: date | None  # None: never
    idle_days: int | None


@dataclass(frozen=True)
class SeatPlan:
    plan: str
    usd_per_seat_month: float
    seats: int
    in_grace: int  # too new to judge
    idle: list[IdleSeat] = field(default_factory=list)

    @property
    def monthly_saving_usd(self) -> float:
        return len(self.idle) * self.usd_per_seat_month

    @property
    def annual_saving_usd(self) -> float:
        return self.monthly_saving_usd * 12


def seat_true_up(
    plan: str,
    roster: list[tuple[Seat, date]],
    last_active: dict[str, date],
    config: Config,
    today: date,
) -> SeatPlan:
    """Seats to reclaim: held, past the grace period, and unused for `inactive_days`."""
    terms, settings = config.seats.plans[plan], config.seats
    idle, in_grace = [], 0
    for seat, first_seen in roster:
        if (today - first_seen).days < settings.grace_days:
            in_grace += 1  # assigned recently: give them time to start
            continue
        last = last_active.get(seat.email)
        days = (today - last).days if last else None
        if days is None or days >= settings.inactive_days:
            idle.append(IdleSeat(seat.email, seat.name, last, days))
    idle.sort(key=lambda s: (s.last_active is not None, s.last_active or date.min, s.email))
    return SeatPlan(plan, terms.usd_per_seat_month, len(roster), in_grace, idle)
