# Runbook

How to run the tool, what each report means, and what to do when something is off.
Written so that someone who didn't build it can operate it.

## The daily and monthly routine

| When | Command | What to do with it |
|------|---------|--------------------|
| Every morning | `finops sync`, then `finops report anomalies --notify` and `finops report caps --notify` | Nothing, unless an alert arrives |
| The 1st of the month | `finops report reconcile --month <last month>` and `finops report seats --notify` | Send the reconciliation to finance; act on the seat list |
| Any time | `finops report <name> --format json` | Feed another tool |

`.github/workflows/reports.yml` runs exactly this on a schedule. Without it, a cron entry
or a scheduled container does the same.

## Set it up

1. `cp .env.example .env` and fill in the credentials you have. A source without one is
   skipped, and `finops sync` says which.
2. Edit `config/finops.yaml`: commitments, owners, tiers, teams, seat plans.
3. `finops check-config`.
4. `finops sync --days 75` the first time, so the anomaly baseline and seat activity
   have history. After that the default 45 days is plenty.

| Credential | Where it comes from | What it can do |
|------------|--------------------|----------------|
| `ANTHROPIC_ADMIN_KEY` | Claude Console → Settings → Admin keys | Read organisation usage and cost. Not an application key |
| `OPENAI_ADMIN_KEY` | OpenAI organization settings → Admin keys | Read organisation costs. Not a project key |
| `OKTA_ORG_URL`, `OKTA_API_TOKEN` | Okta → Security → API → Tokens, from a read-only admin | List group members |
| `SLACK_WEBHOOK_URL` | A Slack incoming webhook | Post to one channel |

All of them are secrets except the Okta URL. They are read from the environment and
never written to the store, the reports or the logs.

## Reading the reports

### Reconciliation
- **"$X of the commitment unused"** — money already committed that the month's usage
  won't reach. If it repeats, the commitment is too large for the next term.
- **"$X over the commitment"** — usage past the commitment, billed at the overage rate.
  Look at "By team" for who, and at the caps report.
- **Mid-month figures are projections** at the pace so far. The report says which day it is.
- **"Spend nobody has claimed"** — a vendor workspace or project that isn't in `owners:`.
  Find who created it, then add it to the config in a pull request.

### Caps
A team is `ok`, `over soft cap` or `over hard cap` for the month. This tool reports; it
does not block. Enforcement at request time is the AI gateway's job.

### Anomalies
A team's spend on one day, far above its own previous two weeks. Typical causes: a batch
job in a loop, a new feature launched, a leaked key. Ask the team first; if nobody knows,
treat it as a leaked key and rotate.

### Seats
People who hold a paid seat and haven't used it for `inactive_days`. Check with them or
their manager before removing them from the group; the report is a list of candidates,
not a decision.

## When something is wrong

### `sync failed: anthropic: the admin credential was refused`
The key is wrong, revoked, or an ordinary API key instead of an admin key. The same
message for `openai` and `okta` means the same thing.

### `sync failed: …: rate limited`
Run it again in a few minutes. Syncing is idempotent: nothing is duplicated.

### `sync failed: …: unexpected response shape`
The vendor changed its API, or this adapter's reading of the documentation was wrong.
The adapters are in `finops/sources/`; each has a test that shows the shape it expects.

### A report is empty
`finops sync` hasn't run for those days, or every source was skipped. Its output says
what it did per source.

### A team shows $0
Its vendor workspace or project id in `owners:` doesn't match the vendor's. The
unallocated section of the reconciliation will show the real id.

### The same alert every morning
It shouldn't: each finding is sent once and remembered in the store. If the store is
recreated on every run (a container without its volume, a cache that isn't restored),
alerts repeat. Keep `data/` between runs.

### Everyone appears as an idle seat
Activity isn't arriving: the Anthropic key can't read Claude Code analytics, or the
plan's `activity` in the config doesn't match the product name. A first sync shorter
than `inactive_days` has the same effect; run `finops sync --days 75`.

### Re-run a report as it was
`finops --today 2026-09-10 report reconcile --month 2026-09` gives the report as of that
day, from whatever the store held.

## Changing things

- **A commitment, a cap, a team, an owner:** edit `config/finops.yaml` in a pull request.
  CI validates it.
- **A new vendor:** an adapter in `finops/sources/` that yields `Cost` records, a line in
  `sync`, and a test against a stand-in. The reports need no change.
- **A new seat product:** a plan in the config, and an adapter that yields `Activity`
  records for it.

## The store

`data/finops.sqlite3`: `costs`, `activity`, `seats`, `alerts`. It holds no credentials.
It does hold email addresses (seat holders and who was active on which day), so treat
it as personal data: keep it where the reports are kept, and don't publish it.
Deleting it loses history and re-sends alerts; a new `sync` rebuilds what the vendors
still serve.
