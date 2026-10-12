"""The store and the command line: syncing, re-running reports, alerting once."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from finops import cli, notify
from finops.model import Cost, Seat
from finops.reports import Anomaly, CapLine
from finops.store import Store


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ANTHROPIC_ADMIN_KEY",
        "OPENAI_ADMIN_KEY",
        "OKTA_ORG_URL",
        "OKTA_API_TOKEN",
        "SLACK_WEBHOOK_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- the store -------------------------------------------------------------------------


def test_a_restated_day_replaces_the_old_figure() -> None:
    store, day = Store(":memory:"), date(2026, 9, 1)
    store.save_costs([Cost(day, "anthropic", "ws", "m", 10.0)])
    store.save_costs([Cost(day, "anthropic", "ws", "m", 12.5)])  # the vendor corrected it
    assert store.costs(day, day) == [Cost(day, "anthropic", "ws", "m", 12.5)]


def test_the_roster_follows_the_identity_provider_and_remembers_when_a_seat_began() -> None:
    store = Store(":memory:")
    alice, bob = Seat("alice@example.com", "Alice", "p"), Seat("bob@example.com", "Bob", "p")
    store.save_seats("p", [alice, bob], date(2026, 8, 1))
    store.save_seats("p", [alice], date(2026, 9, 1))  # bob left the group
    assert store.seats("p") == [(alice, date(2026, 8, 1))]  # and alice's start is kept
    store.save_seats("p", [alice, bob], date(2026, 9, 10))
    assert dict(store.seats("p"))[bob] == date(2026, 9, 10)  # a new seat, a new grace period


def test_an_alert_key_is_new_once() -> None:
    store = Store(":memory:")
    assert store.alert_once("k", date(2026, 9, 1)) is True
    assert store.alert_once("k", date(2026, 9, 2)) is False


# --- the command line ------------------------------------------------------------------


def test_the_demo_finds_one_of_everything(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "demo")
    assert code == 0 and "Every figure below is invented" in out
    assert "$1,449 of the commitment unused" in out and "$441 over the commitment" in out
    assert "| openai/proj_sandbox | $148 |" in out  # spend nobody has claimed
    assert "| eng-backend | standard | $3,784 | $3,500 | $5,000 | 76% | over soft cap |" in out
    assert "| openai | research | $194 | $11 | 17.4x |" in out  # the runaway day
    assert "**Unused for 45 days or more:** 9" in out and "$3,240 a year" in out
    assert run(capsys, "demo")[1] == out  # the same every time


def test_sync_then_report_from_the_local_store(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    db = ["--db", str(tmp_path / "f.sqlite3"), "--today", "2026-09-30"]
    code, out, _ = run(capsys, *db, "sync", "--demo", "--days", "75")
    assert code == 0 and "sample data" in out
    _, md, _ = run(capsys, *db, "report", "reconcile", "--month", "2026-09")
    assert "# Reconciliation, 2026-09" in md and "the month is complete" in md
    _, mid, _ = run(
        capsys, "--db", db[1], "--today", "2026-09-10", "report", "reconcile", "--month", "2026-09"
    )
    assert "day 10 of 30" in mid
    _, raw, _ = run(capsys, *db, "report", "caps", "--format", "json")
    lines = json.loads(raw)
    assert lines[0]["team"] == "compliance" and lines[0]["status"] == "over soft cap"
    _, seats, _ = run(capsys, *db, "report", "seats", "--format", "json")
    assert json.loads(seats)[0]["monthly_saving_usd"] == 270


def test_alerts_are_sent_once(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db = ["--db", str(tmp_path / "f.sqlite3"), "--today", "2026-09-30"]
    run(capsys, *db, "sync", "--demo", "--days", "75")
    anomalies = (*db, "report", "anomalies", "--day", "2026-09-21", "--notify")
    _, _, first = run(capsys, *anomalies)
    assert "research spent $194 on openai on 2026-09-21" in first
    assert run(capsys, *anomalies)[2] == ""  # the same finding, a second run: silence
    _, _, caps = run(capsys, *db, "report", "caps", "--notify")
    assert caps.count("over soft cap") == 2
    _, _, seats = run(capsys, *db, "report", "seats", "--notify")
    assert "9 of 40 seats unused" in seats


def test_a_source_without_credentials_is_skipped_and_said_so(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, _ = run(capsys, "--db", str(tmp_path / "f.sqlite3"), "sync")
    assert code == 0
    assert out.count("skipped") == 3 and "ANTHROPIC_ADMIN_KEY is not set" in out


def test_config_problems_stop_the_run(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert run(capsys, "check-config")[0] == 0
    bad = tmp_path / "bad.yaml"
    bad.write_text("vendors: {}\ntiers: {}\nteams: {eng: {tier: gold}}\n")
    code, _, err = run(capsys, "--config", str(bad), "check-config")
    assert code == 2 and "unknown tier" in err
    assert run(capsys, "--config", str(tmp_path / "missing.yaml"), "check-config")[0] == 2


# --- alerts ----------------------------------------------------------------------------


def test_an_alert_is_posted_as_text_and_a_failure_is_not_fatal() -> None:
    posted: list[dict[str, str]] = []

    def ok(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    assert notify.send("https://hooks.example/T/B/x", "hello", httpx.MockTransport(ok)) is True
    assert posted == [{"text": "hello"}]
    assert (
        notify.send(
            "https://hooks.example/x", "t", httpx.MockTransport(lambda r: httpx.Response(404))
        )
        is False
    )

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert notify.send("https://hooks.example/x", "t", httpx.MockTransport(down)) is False


def test_alert_wording() -> None:
    spike = Anomaly(date(2026, 9, 21), "openai", "research", 194.0, 11.0, 30.0)
    assert notify.anomaly_text(spike).endswith("(17.6x a typical day of $11).")
    line = CapLine("desk", "standard", 5200.0, 3500.0, 5000.0)
    assert "desk is over hard cap for 2026-09: $5,200 of $5,000 (104%)" in notify.cap_text(
        line, "2026-09"
    )
