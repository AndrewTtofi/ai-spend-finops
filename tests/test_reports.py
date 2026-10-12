"""The four reports, on small hand-made inputs where the right answer is obvious."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from finops import reports
from finops.config import Config, load
from finops.model import Cost, Seat

CONFIG = Config.model_validate(
    {
        "vendors": {
            "anthropic": {"commitment_usd_per_month": 3000},
            "openai": {"commitment_usd_per_month": 1000},
        },
        "owners": {"anthropic": {"ws_eng": "eng"}, "openai": {"proj_desk": "desk"}},
        "tiers": {"standard": {"soft_usd": 800, "hard_usd": 1000}},
        "teams": {"eng": {"tier": "standard"}, "desk": {"tier": "standard"}},
        "seats": {
            "inactive_days": 45,
            "grace_days": 14,
            "plans": {
                "claude-team": {
                    "usd_per_seat_month": 30,
                    "okta_group_id": "00g1",
                    "activity": "claude_code",
                }
            },
        },
        "anomalies": {"baseline_days": 14, "threshold": 3.5, "min_usd": 25},
    }
)
SEP = date(2026, 9, 1)


def daily(vendor: str, scope: str, usd: float, days: int, start: date = SEP) -> list[Cost]:
    return [Cost(start + timedelta(days=n), vendor, scope, "model", usd) for n in range(days)]


# --- config ----------------------------------------------------------------------------


def test_the_shipped_config_is_valid() -> None:
    config = load("config/finops.yaml")
    assert config.owner("anthropic", "wrkspc_eng") == "eng-backend"
    assert config.owner("anthropic", "default") is None  # nobody has claimed it


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"teams": {"eng": {"tier": "gold"}}}, "unknown tier"),
        ({"owners": {"anthropic": {"ws": "nobody"}}}, "unknown team"),
        ({"owners": {"mistral": {"x": "eng"}}}, "unknown vendor"),
        ({"tiers": {"standard": {"soft_usd": 10, "hard_usd": 5}}}, "soft_usd"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_a_config_that_disagrees_with_itself_is_refused(change: dict, says: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValidationError, match=says):
        Config.model_validate({**CONFIG.model_dump(), **change})


# --- reconcile -------------------------------------------------------------------------


def test_mid_month_the_projection_assumes_the_pace_so_far() -> None:
    costs = daily("anthropic", "ws_eng", 100, 30) + daily("openai", "proj_desk", 50, 30)
    result = reports.reconcile(costs, CONFIG, "2026-09", today=date(2026, 9, 10))
    anthropic, openai = result.vendors
    assert (anthropic.actual_usd, anthropic.days_elapsed, anthropic.days_in_month) == (1000, 10, 30)
    assert anthropic.projected_usd == pytest.approx(
        3000
    ) and anthropic.variance_usd == pytest.approx(0)
    assert openai.projected_usd == pytest.approx(1500)
    assert openai.variance_usd == pytest.approx(500)  # heading over the commitment
    assert result.teams == {"eng": 1000, "desk": 500}  # only the ten days so far


def test_a_finished_month_reports_what_was_billed_and_what_went_unused() -> None:
    costs = daily("anthropic", "ws_eng", 60, 30) + daily(
        "anthropic", "ws_eng", 999, 5, date(2026, 10, 1)
    )
    result = reports.reconcile(costs, CONFIG, "2026-09", today=date(2026, 10, 5))
    line = result.vendors[0]
    assert (line.actual_usd, line.projected_usd) == (1800, 1800)  # October is not counted
    assert line.variance_usd == -1200 and line.used_share == pytest.approx(0.6)
    assert result.as_of == date(2026, 9, 30)


def test_spend_nobody_owns_is_named() -> None:
    costs = daily("anthropic", "ws_eng", 10, 2) + daily("openai", "proj_shadow", 7, 2)
    result = reports.reconcile(costs, CONFIG, "2026-09", today=date(2026, 9, 2))
    assert result.unallocated == {"openai/proj_shadow": 14}
    assert result.teams == {"eng": 20, reports.UNALLOCATED: 14}


# --- caps ------------------------------------------------------------------------------


def test_caps_have_three_states_and_the_closest_to_the_cap_comes_first() -> None:
    costs = daily("anthropic", "ws_eng", 85, 10) + daily("openai", "proj_desk", 101, 10)
    eng_first = reports.caps(costs, CONFIG, "2026-09", today=date(2026, 9, 10))
    assert [(c.team, c.status) for c in eng_first] == [
        ("desk", "over hard cap"),
        ("eng", "over soft cap"),
    ]
    assert eng_first[1].used_share == pytest.approx(0.85)
    early = reports.caps(costs, CONFIG, "2026-09", today=date(2026, 9, 2))
    assert {c.status for c in early} == {"ok"}
    assert [c.spent_usd for c in reports.caps([], CONFIG, "2026-09", SEP)] == [0, 0]


# --- anomalies -------------------------------------------------------------------------

DAY = SEP + timedelta(days=14)


def spend(*amounts: float, scope: str = "ws_eng", vendor: str = "anthropic") -> list[Cost]:
    """`amounts` for the 14 days before DAY, then DAY itself."""
    return [Cost(SEP + timedelta(days=n), vendor, scope, "m", usd) for n, usd in enumerate(amounts)]


def test_a_day_far_above_the_teams_own_recent_days_is_flagged() -> None:
    usual = [100, 110, 95, 105, 100, 90, 108, 102, 97, 103, 99, 101, 104, 96]
    (found,) = reports.anomalies(spend(*usual, 900), CONFIG, DAY)
    assert (found.vendor, found.team, found.usd) == ("anthropic", "eng", 900)
    assert found.typical_usd == pytest.approx(100.5) and found.score > 3.5
    assert found.key == f"anomaly:{DAY.isoformat()}:anthropic:eng"
    assert reports.anomalies(spend(*usual, 112), CONFIG, DAY) == []  # an ordinary busy day


def test_an_earlier_spike_does_not_hide_a_new_one() -> None:
    usual = [100, 110, 95, 2000, 100, 90, 108, 102, 97, 103, 99, 101, 104, 96]
    assert len(reports.anomalies(spend(*usual, 900), CONFIG, DAY)) == 1  # the median holds


def test_small_money_is_not_an_alert_however_unusual() -> None:
    assert reports.anomalies(spend(*[2.0] * 14, 20), CONFIG, DAY) == []  # 10x, and $18
    (found,) = reports.anomalies(spend(*[2.0] * 14, 60), CONFIG, DAY)  # flat baseline, real money
    assert found.score == float("inf")


def test_new_spend_from_an_unowned_scope_is_flagged_as_unallocated() -> None:
    costs = [Cost(DAY, "openai", "proj_shadow", "m", 400)]
    (found,) = reports.anomalies(costs, CONFIG, DAY)
    assert (found.team, found.typical_usd) == (reports.UNALLOCATED, 0)


# --- seats -----------------------------------------------------------------------------

TODAY = date(2026, 9, 30)


def test_seats_to_reclaim_are_held_past_grace_and_unused() -> None:
    long_ago = TODAY - timedelta(days=90)
    roster = [
        (Seat("active@example.com", "Active", "claude-team"), long_ago),
        (Seat("never@example.com", "Never", "claude-team"), long_ago),
        (Seat("lapsed@example.com", "Lapsed", "claude-team"), long_ago),
        (Seat("edge@example.com", "Edge", "claude-team"), long_ago),
        (Seat("new@example.com", "New", "claude-team"), TODAY - timedelta(days=3)),
    ]
    last_active = {
        "active@example.com": TODAY - timedelta(days=2),
        "lapsed@example.com": TODAY - timedelta(days=60),
        "edge@example.com": TODAY - timedelta(days=44),  # one day short of the threshold
    }
    plan = reports.seat_true_up("claude-team", roster, last_active, CONFIG, TODAY)
    assert (plan.seats, plan.in_grace) == (5, 1)  # the new seat is not judged yet
    assert [(s.email, s.idle_days) for s in plan.idle] == [
        ("never@example.com", None),
        ("lapsed@example.com", 60),
    ]
    assert (plan.monthly_saving_usd, plan.annual_saving_usd) == (60, 720)


def test_month_bounds() -> None:
    assert reports.month_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))
