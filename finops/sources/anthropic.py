"""Anthropic's Admin API: what the organisation was billed, and who used Claude Code.

    GET /v1/organizations/cost_report                 daily cost, in cents, by workspace
    GET /v1/organizations/usage_report/claude_code    one day's activity per user

Both need an Admin API key (`sk-ant-admin…`), which is not the key applications use.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from finops.model import Activity, Cost
from finops.sources.http import SourceError, get_json

BASE = "https://api.anthropic.com"
PRODUCT = "claude_code"


class Anthropic:
    def __init__(self, admin_key: str, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=BASE,
            timeout=30.0,
            transport=transport,
            headers={
                "x-api-key": admin_key,
                "anthropic-version": "2023-06-01",
                "user-agent": "ai-spend-finops/0.1",
            },
        )

    def _pages(self, path: str, params: list[tuple[str, str]]) -> Iterator[dict[str, Any]]:
        page: str | None = None
        while True:
            query = [*params, *([("page", page)] if page else [])]
            body, _ = get_json(self._client, "anthropic", path, query)
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise SourceError("anthropic: unexpected response shape")
            yield from (row for row in body["data"] if isinstance(row, dict))
            page = body.get("next_page")
            if not (body.get("has_more") and page):
                return

    def costs(self, start: date, end: date) -> Iterator[Cost]:
        """Billed cost per day in [start, end], by workspace and line item."""
        params = [
            ("starting_at", f"{start.isoformat()}T00:00:00Z"),
            ("ending_at", f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z"),  # exclusive
            ("group_by[]", "workspace_id"),
            ("group_by[]", "description"),
            ("limit", "31"),
        ]
        for bucket in self._pages("/v1/organizations/cost_report", params):
            day = datetime.fromisoformat(str(bucket["starting_at"])).astimezone(UTC).date()
            for row in bucket.get("results") or []:
                yield Cost(
                    day=day,
                    vendor="anthropic",
                    # The default workspace has a null id.
                    scope=str(row.get("workspace_id") or "default"),
                    item=str(row.get("model") or row.get("description") or "other"),
                    # The amount is a decimal string in cents.
                    usd=float(row["amount"]) / 100,
                )

    def activity(self, day: date) -> Iterator[Activity]:
        """Who used Claude Code on one day. API-key actors have no person to name."""
        params = [("starting_at", day.isoformat()), ("limit", "1000")]
        for row in self._pages("/v1/organizations/usage_report/claude_code", params):
            actor = row.get("actor") or {}
            email = actor.get("email_address")
            if actor.get("type") == "user_actor" and isinstance(email, str):
                yield Activity(day=day, email=email.lower(), product=PRODUCT)
