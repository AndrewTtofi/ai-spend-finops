"""The vendor and identity-provider adapters, against stand-ins built from their
documented requests and responses."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from finops.model import Activity, Cost, Seat
from finops.sources.anthropic import Anthropic
from finops.sources.http import SourceError
from finops.sources.okta import Okta
from finops.sources.openai import OpenAI

ADMIN_KEY = "sk-ant-admin01-not-a-real-key"


def transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# --- Anthropic -------------------------------------------------------------------------


def test_anthropic_costs_are_cents_by_workspace_across_pages() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "page" not in request.url.params:
            bucket = {
                "starting_at": "2026-09-01T00:00:00Z",
                "ending_at": "2026-09-02T00:00:00Z",
                "results": [
                    {
                        "amount": "12345.6",
                        "currency": "USD",
                        "workspace_id": "ws_eng",
                        "model": "claude-x",
                        "description": "Claude X Usage",
                    },
                    {
                        "amount": "50",
                        "currency": "USD",
                        "workspace_id": None,
                        "description": "Web Search Usage",
                    },
                ],
            }
            return httpx.Response(200, json={"data": [bucket], "has_more": True, "next_page": "p2"})
        bucket = {
            "starting_at": "2026-09-02T00:00:00Z",
            "results": [{"amount": "100", "workspace_id": "ws_eng", "model": "claude-x"}],
        }
        return httpx.Response(200, json={"data": [bucket], "has_more": False, "next_page": None})

    costs = list(Anthropic(ADMIN_KEY, transport(handler)).costs(date(2026, 9, 1), date(2026, 9, 2)))
    assert costs == [
        Cost(date(2026, 9, 1), "anthropic", "ws_eng", "claude-x", 123.456),
        Cost(date(2026, 9, 1), "anthropic", "default", "Web Search Usage", 0.5),  # no workspace
        Cost(date(2026, 9, 2), "anthropic", "ws_eng", "claude-x", 1.0),
    ]
    first, second = seen
    assert first.url.path == "/v1/organizations/cost_report"
    assert first.headers["x-api-key"] == ADMIN_KEY and first.headers["anthropic-version"]
    assert first.url.params["starting_at"] == "2026-09-01T00:00:00Z"
    assert first.url.params["ending_at"] == "2026-09-03T00:00:00Z"  # the end day is included
    assert first.url.params.get_list("group_by[]") == ["workspace_id", "description"]
    assert second.url.params["page"] == "p2"


def test_anthropic_activity_names_people_not_api_keys() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/organizations/usage_report/claude_code"
        assert request.url.params["starting_at"] == "2026-09-08"
        rows = [
            {"actor": {"type": "user_actor", "email_address": "Dev@Example.com"}},
            {"actor": {"type": "api_actor", "api_key_name": "ci-bot"}},
            {"actor": None},
        ]
        return httpx.Response(200, json={"data": rows, "has_more": False, "next_page": None})

    found = list(Anthropic(ADMIN_KEY, transport(handler)).activity(date(2026, 9, 8)))
    assert found == [Activity(date(2026, 9, 8), "dev@example.com", "claude_code")]


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (httpx.Response(401, json={"error": "bad key"}), "credential was refused"),
        (httpx.Response(429), "rate limited"),
        (httpx.Response(500), "HTTP 500"),
        (httpx.Response(200, text="<html>"), "not JSON"),
        (httpx.Response(200, json={"oops": True}), "unexpected response shape"),
    ],
)
def test_a_source_that_fails_says_so_without_the_credential(
    answer: httpx.Response, says: str
) -> None:
    client = Anthropic(ADMIN_KEY, transport(lambda request: answer))
    with pytest.raises(SourceError) as err:
        list(client.costs(date(2026, 9, 1), date(2026, 9, 1)))
    assert says in str(err.value) and ADMIN_KEY not in str(err.value)


def test_an_unreachable_source_is_an_error_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(SourceError, match="could not be reached"):
        list(OpenAI("sk-admin", transport(handler)).costs(date(2026, 9, 1), date(2026, 9, 1)))


# --- OpenAI ----------------------------------------------------------------------------


def test_openai_costs_are_dollars_by_project_across_pages() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        first = "page" not in request.url.params
        row = {
            "object": "organization.costs.result",
            "amount": {"value": 6.5 if first else 2.0, "currency": "usd"},
            "line_item": "gpt, input",
            "project_id": "proj_desk" if first else None,
        }
        start = 1788220800 if first else 1788307200  # 2026-09-01 and 09-02, 00:00 UTC
        bucket = {
            "object": "bucket",
            "start_time": start,
            "end_time": start + 86400,
            "results": [row],
        }
        return httpx.Response(
            200,
            json={
                "object": "page",
                "data": [bucket],
                "has_more": first,
                "next_page": "n2" if first else None,
            },
        )

    costs = list(OpenAI("sk-admin", transport(handler)).costs(date(2026, 9, 1), date(2026, 9, 2)))
    assert costs == [
        Cost(date(2026, 9, 1), "openai", "proj_desk", "gpt, input", 6.5),
        Cost(date(2026, 9, 2), "openai", "default", "gpt, input", 2.0),
    ]
    first_request = seen[0]
    assert first_request.url.path == "/v1/organization/costs"
    assert first_request.headers["authorization"] == "Bearer sk-admin"
    assert first_request.url.params["start_time"] == "1788220800"
    assert first_request.url.params["end_time"] == "1788393600"  # the end day is included
    assert first_request.url.params.get_list("group_by") == ["project_id", "line_item"]
    assert seen[1].url.params["page"] == "n2"


# --- Okta ------------------------------------------------------------------------------

ORG = "https://example.okta.com"


def user(email: str, status: str = "ACTIVE", first: str = "A", last: str = "B") -> dict[str, Any]:
    return {
        "id": email,
        "status": status,
        "profile": {"email": email, "firstName": first, "lastName": last},
    }


def test_okta_seats_are_the_active_members_of_the_group_across_pages() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "after" not in request.url.params:
            more = f"{ORG}/api/v1/groups/00g1/users?after=u2&limit=200"
            link = f'<{more}>; rel="next", <{ORG}/x>; rel="self"'
            body = [
                user("Alice@Example.com", first="Alice", last="Example"),
                user("gone@example.com", "DEPROVISIONED"),
            ]
            return httpx.Response(200, json=body, headers={"link": link})
        return httpx.Response(200, json=[user("bob@example.com", first="", last="")])

    seats = list(Okta(ORG + "/", "token", transport(handler)).seats("00g1", "claude-team"))
    assert seats == [
        Seat("alice@example.com", "Alice Example", "claude-team"),
        Seat("bob@example.com", "bob@example.com", "claude-team"),  # no name: the email
    ]
    assert seen[0].headers["authorization"] == "SSWS token"
    assert seen[0].url.path == "/api/v1/groups/00g1/users" and len(seen) == 2


def test_okta_paging_never_follows_a_link_to_another_host() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        link = '<https://evil.example/api/v1/groups/00g1/users?after=x>; rel="next"'
        return httpx.Response(200, json=[user("a@example.com")], headers={"link": link})

    assert len(list(Okta(ORG, "token", transport(handler)).seats("00g1", "p"))) == 1
    assert seen == ["example.okta.com"]  # the token went nowhere else


def test_okta_refuses_a_group_id_that_is_not_one() -> None:
    with pytest.raises(SourceError, match="group id"):
        list(
            Okta(ORG, "token", transport(lambda r: httpx.Response(200, json=[]))).seats(
                "../users", "p"
            )
        )
