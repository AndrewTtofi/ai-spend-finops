"""`finops`: sync what the vendors and the identity provider say, then report on it.

    finops check-config
    finops sync [--days 45] [--demo]
    finops report reconcile [--month 2026-09]
    finops report caps      [--month 2026-09]
    finops report anomalies [--day 2026-09-21]
    finops report seats
    finops demo                         # sample data, all four reports, no credentials

Credentials come from the environment (see .env.example). Reports read the local store,
so they can be re-run without calling anyone.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta

from finops import notify, render, reports
from finops.config import Config, load
from finops.sources import demo
from finops.sources.anthropic import Anthropic
from finops.sources.http import SourceError
from finops.sources.okta import Okta
from finops.sources.openai import OpenAI
from finops.store import Store

DEMO_TODAY = date(2026, 9, 30)  # sample data is anchored here, so the demo never changes


def sync(config: Config, store: Store, today: date, days: int, use_demo: bool) -> list[str]:
    """Fetch the last `days` days into the store. → one line per source, done or skipped."""
    start = today - timedelta(days=days)
    out: list[str] = []
    if use_demo:
        out.append(f"demo: {store.save_costs(demo.costs(today, days))} cost rows (sample data)")
        store.save_activity(demo.activity(today, days))
        for plan in config.seats.plans:
            # First seen two months ago, so nobody is still in the grace period.
            store.save_seats(plan, demo.seats(plan), today - timedelta(days=60))
            out.append(f"demo: {store.save_seats(plan, demo.seats(plan), today)} seats")
        return out
    env = os.environ
    if key := env.get("ANTHROPIC_ADMIN_KEY"):
        anthropic = Anthropic(key)
        out.append(f"anthropic: {store.save_costs(anthropic.costs(start, today))} cost rows")
        seen = 0
        for offset in range(days + 1):  # this endpoint answers for one day at a time
            seen += store.save_activity(anthropic.activity(today - timedelta(days=offset)))
        out.append(f"anthropic: {seen} user-days of Claude Code activity")
    else:
        out.append("anthropic: skipped (ANTHROPIC_ADMIN_KEY is not set)")
    if key := env.get("OPENAI_ADMIN_KEY"):
        out.append(f"openai: {store.save_costs(OpenAI(key).costs(start, today))} cost rows")
    else:
        out.append("openai: skipped (OPENAI_ADMIN_KEY is not set)")
    if (org := env.get("OKTA_ORG_URL")) and (token := env.get("OKTA_API_TOKEN")):
        okta = Okta(org, token)
        for plan, terms in config.seats.plans.items():
            count = store.save_seats(plan, okta.seats(terms.okta_group_id, plan), today)
            out.append(f"okta: {count} seats for {plan}")
    else:
        out.append("okta: skipped (OKTA_ORG_URL and OKTA_API_TOKEN are not set)")
    return out


def _alert(store: Store, key: str, text: str, today: date) -> None:
    """Send once per key. Without a webhook the alert is printed instead."""
    if not store.alert_once(key, today):
        return
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print(f"[alert, no webhook configured] {text}", file=sys.stderr)
    elif not notify.send(url, text):
        print(f"[alert not delivered] {text}", file=sys.stderr)


def report(args: argparse.Namespace, config: Config, store: Store, today: date) -> str:
    as_json = args.format == "json"
    month = args.month or today.strftime("%Y-%m")
    window = max(config.anomalies.baseline_days, 62)
    if args.name == "reconcile":
        start, end = reports.month_bounds(month)
        result = reports.reconcile(store.costs(start, end), config, month, today)
        return (
            json.dumps(render.plain(result), indent=2) if as_json else render.reconciliation(result)
        )
    if args.name == "caps":
        start, end = reports.month_bounds(month)
        lines = reports.caps(store.costs(start, end), config, month, today)
        if args.notify:
            for line in lines:
                if line.status != "ok":
                    key = f"cap:{month}:{line.team}:{line.status}"
                    _alert(store, key, notify.cap_text(line, month), today)
        return (
            json.dumps(render.plain(lines), indent=2) if as_json else render.cap_lines(lines, month)
        )
    if args.name == "anomalies":
        # The vendors' figures for a day settle over a few hours: judge yesterday.
        day = date.fromisoformat(args.day) if args.day else today - timedelta(days=1)
        found = reports.anomalies(store.costs(day - timedelta(days=window), day), config, day)
        if args.notify:
            for a in found:
                _alert(store, a.key, notify.anomaly_text(a), today)
        return (
            json.dumps(render.plain(found), indent=2)
            if as_json
            else render.anomaly_lines(found, day)
        )
    plans = [
        reports.seat_true_up(
            plan, store.seats(plan), store.last_active(terms.activity), config, today
        )
        for plan, terms in config.seats.plans.items()
    ]
    if args.notify:
        for plan in plans:
            if plan.idle:
                key = f"seats:{today.strftime('%Y-%m')}:{plan.plan}"
                _alert(store, key, notify.seats_text(plan), today)
    if as_json:
        return json.dumps(render.plain(plans), indent=2)
    return render.seat_plans(plans, today, config.seats.inactive_days)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="finops", description="AI spend and seat FinOps.")
    p.add_argument("--config", default="config/finops.yaml")
    p.add_argument("--db", default="data/finops.sqlite3")
    p.add_argument("--today", help="pretend it is this day (YYYY-MM-DD); for re-running a report")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config", help="validate the config file")
    s = sub.add_parser("sync", help="fetch from the vendors and the identity provider")
    s.add_argument("--days", type=int, default=45)
    s.add_argument("--demo", action="store_true", help="load sample data instead")
    r = sub.add_parser("report", help="print a report from the local store")
    r.add_argument("name", choices=["reconcile", "caps", "anomalies", "seats"])
    r.add_argument("--month", help="YYYY-MM (default: this month)")
    r.add_argument("--day", help="YYYY-MM-DD, for anomalies (default: yesterday)")
    r.add_argument("--format", choices=["md", "json"], default="md")
    r.add_argument("--notify", action="store_true", help="send alerts to SLACK_WEBHOOK_URL")
    sub.add_parser("demo", help="sample data and all four reports; needs no credentials")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load(args.config)
    except (OSError, ValueError) as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2
    if args.command == "check-config":
        print(f"{args.config}: ok ({len(config.teams)} teams, {len(config.vendors)} vendors)")
        return 0
    today = date.fromisoformat(args.today) if args.today else datetime.now(UTC).date()
    if args.command == "demo":
        store = Store(":memory:")
        sync(config, store, DEMO_TODAY, 75, use_demo=True)
        print("> Sample data. Every figure below is invented.\n")
        runaway = (DEMO_TODAY - timedelta(days=demo.RUNAWAY[2])).isoformat()
        for name, day in (
            ("reconcile", None),
            ("caps", None),
            ("anomalies", runaway),
            ("seats", None),
        ):
            ns = argparse.Namespace(name=name, month=None, day=day, format="md", notify=False)
            print(report(ns, config, store, DEMO_TODAY))
        return 0
    store = Store(args.db)
    try:
        if args.command == "sync":
            for line in sync(config, store, today, args.days, args.demo):
                print(line)
            return 0
        print(report(args, config, store, today))
        return 0
    except SourceError as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
