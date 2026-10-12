"""OpenAI's organisation costs API: what was billed per day, by project and line item.

    GET /v1/organization/costs

Needs an admin key, which is not the key applications use.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta

import httpx

from finops.model import Cost
from finops.sources.http import SourceError, get_json

BASE = "https://api.openai.com"


def _unix(day: date) -> int:
    return int(datetime.combine(day, time.min, UTC).timestamp())


class OpenAI:
    def __init__(self, admin_key: str, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=BASE,
            timeout=30.0,
            transport=transport,
            headers={"authorization": f"Bearer {admin_key}", "user-agent": "ai-spend-finops/0.1"},
        )

    def costs(self, start: date, end: date) -> Iterator[Cost]:
        """Billed cost per day in [start, end], by project and line item."""
        params: list[tuple[str, str]] = [
            ("start_time", str(_unix(start))),
            ("end_time", str(_unix(end + timedelta(days=1)))),  # exclusive
            ("bucket_width", "1d"),
            ("group_by", "project_id"),
            ("group_by", "line_item"),
            ("limit", "31"),
        ]
        page: str | None = None
        while True:
            query = [*params, *([("page", page)] if page else [])]
            body, _ = get_json(self._client, "openai", "/v1/organization/costs", query)
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise SourceError("openai: unexpected response shape")
            for bucket in body["data"]:
                day = datetime.fromtimestamp(int(bucket["start_time"]), UTC).date()
                for row in bucket.get("results") or []:
                    amount = row.get("amount") or {}
                    yield Cost(
                        day=day,
                        vendor="openai",
                        scope=str(row.get("project_id") or "default"),
                        item=str(row.get("line_item") or "other"),
                        usd=float(amount.get("value") or 0.0),  # dollars
                    )
            page = body.get("next_page")
            if not (body.get("has_more") and page):
                return
