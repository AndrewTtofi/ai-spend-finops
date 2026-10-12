"""Reports as Markdown, for a person or a pull request, and as plain data for JSON."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date
from typing import Any

from finops.reports import Anomaly, CapLine, Reconciliation, SeatPlan


def usd(amount: float) -> str:
    return f"-${-amount:,.0f}" if amount < 0 else f"${amount:,.0f}"


def table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def reconciliation(r: Reconciliation) -> str:
    done = r.vendors and r.vendors[0].days_elapsed == r.vendors[0].days_in_month
    title = f"# Reconciliation, {r.month}"
    when = (
        "the month is complete"
        if done
        else (
            f"as of {r.as_of.isoformat()}, day {r.vendors[0].days_elapsed} of "
            f"{r.vendors[0].days_in_month}; projections assume the pace so far"
            if r.vendors
            else f"as of {r.as_of.isoformat()}"
        )
    )
    rows = []
    for v in r.vendors:
        if v.variance_usd < 0:
            verdict = f"{usd(-v.variance_usd)} of the commitment unused"
        elif v.variance_usd > 0:
            verdict = f"{usd(v.variance_usd)} over the commitment"
        else:
            verdict = "on the commitment"
        rows.append(
            [
                v.vendor,
                usd(v.commitment_usd),
                usd(v.actual_usd),
                usd(v.projected_usd),
                f"{v.used_share:.0%}",
                verdict,
            ]
        )
    out = [title, "", f"_{when}._", "", "## Against commitments", ""]
    out.append(
        table(["Vendor", "Committed", "Billed so far", "Projected", "Of commitment", ""], rows)
    )
    out += ["", "## By team", ""]
    total = sum(r.teams.values()) or 1.0
    out.append(
        table(
            ["Team", "Billed", "Share"],
            [
                [t, usd(v), f"{v / total:.0%}"]
                for t, v in sorted(r.teams.items(), key=lambda x: -x[1])
            ],
        )
    )
    if r.unallocated:
        out += ["", "## Spend nobody has claimed", ""]
        out.append("Add these to `owners:` in the config, or find out who created them.")
        out.append("")
        out.append(
            table(
                ["Vendor / scope", "Billed"],
                [[k, usd(v)] for k, v in sorted(r.unallocated.items(), key=lambda x: -x[1])],
            )
        )
    return "\n".join(out) + "\n"


def cap_lines(lines: list[CapLine], month: str) -> str:
    rows = [
        [
            ln.team,
            ln.tier,
            usd(ln.spent_usd),
            usd(ln.soft_usd),
            usd(ln.hard_usd),
            f"{ln.used_share:.0%}",
            ln.status,
        ]
        for ln in lines
    ]
    head = ["Team", "Tier", "Spent", "Soft cap", "Hard cap", "Of hard cap", "Status"]
    return f"# Spend against caps, {month}\n\n{table(head, rows)}\n"


def anomaly_lines(found: list[Anomaly], day: date) -> str:
    if not found:
        return f"# Spend anomalies, {day.isoformat()}\n\nNone.\n"
    rows = [
        [
            a.vendor,
            a.team,
            usd(a.usd),
            usd(a.typical_usd),
            f"{a.usd / a.typical_usd:.1f}x" if a.typical_usd else "new",
        ]
        for a in found
    ]
    head = ["Vendor", "Team", "That day", "A typical day", "Multiple"]
    return f"# Spend anomalies, {day.isoformat()}\n\n{table(head, rows)}\n"


def seat_plans(plans: list[SeatPlan], today: date, inactive_days: int) -> str:
    out = [f"# Seat true-up, {today.isoformat()}", ""]
    for p in plans:
        out += [
            f"## {p.plan}",
            "",
            f"- **Seats held:** {p.seats} at {usd(p.usd_per_seat_month)} a month",
            f"- **Too new to judge:** {p.in_grace}",
            f"- **Unused for {inactive_days} days or more:** {len(p.idle)}",
            f"- **Saving if reclaimed:** {usd(p.monthly_saving_usd)} a month, "
            f"{usd(p.annual_saving_usd)} a year",
            "",
        ]
        if p.idle:
            rows = [
                [
                    s.name,
                    s.email,
                    s.last_active.isoformat() if s.last_active else "never",
                    str(s.idle_days) if s.idle_days is not None else "-",
                ]
                for s in p.idle
            ]
            out += [table(["Name", "Email", "Last used", "Days idle"], rows), ""]
    return "\n".join(out)


def plain(value: Any) -> Any:
    """Dataclasses, dates and computed properties as JSON-ready data."""
    if is_dataclass(value) and not isinstance(value, type):
        data = {k: plain(v) for k, v in asdict(value).items()}
        for name in dir(type(value)):
            if isinstance(getattr(type(value), name, None), property):
                data[name] = plain(getattr(value, name))
        return data
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    if isinstance(value, float):
        return None if value == float("inf") else round(value, 2)
    return value
