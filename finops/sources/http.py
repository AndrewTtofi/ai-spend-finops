"""One way of calling an admin API, shared by the sources."""

from __future__ import annotations

from typing import Any

import httpx


class SourceError(RuntimeError):
    """A source could not be read. The message names the source and the HTTP status,
    never a credential."""


def get_json(
    client: httpx.Client, source: str, url: str, params: Any = None
) -> tuple[Any, httpx.Headers]:
    try:
        resp = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise SourceError(f"{source}: could not be reached ({type(exc).__name__})") from None
    if resp.status_code in (401, 403):
        raise SourceError(f"{source}: the admin credential was refused (HTTP {resp.status_code})")
    if resp.status_code == 429:
        raise SourceError(f"{source}: rate limited; run the sync again later")
    if resp.status_code != 200:
        raise SourceError(f"{source}: HTTP {resp.status_code}")
    try:
        return resp.json(), resp.headers
    except ValueError:
        raise SourceError(f"{source}: the response was not JSON") from None
