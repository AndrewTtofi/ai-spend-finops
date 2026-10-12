"""Alerts to a Slack-compatible incoming webhook.

The URL is a secret (anyone holding it can post to the channel), so it comes from the
environment and is never logged. An alert that can't be delivered is reported, and does
not stop the report that produced it.
"""

from __future__ import annotations

import logging

import httpx

from finops.render import usd
from finops.reports import Anomaly, CapLine, SeatPlan

log = logging.getLogger(__name__)


def send(webhook_url: str, text: str, transport: httpx.BaseTransport | None = None) -> bool:
    try:
        with httpx.Client(timeout=10.0, transport=transport) as client:
            resp = client.post(webhook_url, json={"text": text})
    except httpx.HTTPError as exc:
        log.error("alert not delivered: %s", type(exc).__name__)
        return False
    if resp.status_code >= 300:
        log.error("alert not delivered: HTTP %d", resp.status_code)
        return False
    return True


def anomaly_text(a: Anomaly) -> str:
    multiple = f"{a.usd / a.typical_usd:.1f}x a typical day" if a.typical_usd else "new spend"
    return (
        f":rotating_light: AI spend: {a.team} spent {usd(a.usd)} on {a.vendor} on "
        f"{a.day.isoformat()} ({multiple} of {usd(a.typical_usd)})."
    )


def cap_text(line: CapLine, month: str) -> str:
    icon = ":octagonal_sign:" if line.status == "over hard cap" else ":warning:"
    return (
        f"{icon} AI spend: {line.team} is {line.status} for {month}: {usd(line.spent_usd)} "
        f"of {usd(line.hard_usd)} ({line.used_share:.0%})."
    )


def seats_text(plan: SeatPlan) -> str:
    return (
        f":seat: {plan.plan}: {len(plan.idle)} of {plan.seats} seats unused. Reclaiming them "
        f"saves {usd(plan.monthly_saving_usd)} a month ({usd(plan.annual_saving_usd)} a year)."
    )
