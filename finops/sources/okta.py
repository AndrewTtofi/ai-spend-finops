"""Okta: who holds a seat. A seat is membership of the group that assigns the product.

    GET /api/v1/groups/{id}/users

Needs an API token with read access to groups (`SSWS …`). Only active users count: a
deactivated user's seat is already gone.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import httpx

from finops.model import Seat
from finops.sources.http import SourceError, get_json

_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class Okta:
    def __init__(
        self, org_url: str, api_token: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._org = org_url.rstrip("/")
        self._client = httpx.Client(
            timeout=30.0,
            transport=transport,
            headers={"authorization": f"SSWS {api_token}", "accept": "application/json"},
        )

    def seats(self, group_id: str, plan: str) -> Iterator[Seat]:
        if not re.fullmatch(r"[A-Za-z0-9]{1,64}", group_id):
            raise SourceError("okta: a group id is letters and digits")
        url: str | None = f"{self._org}/api/v1/groups/{group_id}/users?limit=200"
        while url:
            body, headers = get_json(self._client, "okta", url)
            if not isinstance(body, list):
                raise SourceError("okta: unexpected response shape")
            for user in body:
                profile = user.get("profile") or {}
                email = profile.get("email") or profile.get("login")
                if user.get("status") != "ACTIVE" or not isinstance(email, str):
                    continue
                name = f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip()
                yield Seat(email=email.lower(), name=name or email, plan=plan)
            # Okta pages with a Link header. Only follow it within the same organisation:
            # the token must never be sent to a host a response names.
            link = _NEXT.search(headers.get("link", ""))
            url = link.group(1) if link and link.group(1).startswith(self._org + "/") else None
