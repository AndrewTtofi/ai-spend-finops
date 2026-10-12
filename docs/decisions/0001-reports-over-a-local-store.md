# 0001 — Pure reports over a local store, with decisions in one config file

- **Status:** accepted
- **Date:** 2026-10-13

## Context
A company's AI spend is billed in several places: per-token API usage at each vendor, and
per-seat subscriptions assigned through the identity provider. The questions finance and
the platform team ask every month cut across all of them:

- Are we using what we committed to each vendor, or paying for nothing, or heading over?
- Which team spent it, and is any team past what it was given?
- Did something go wrong yesterday?
- Who holds a paid seat they don't use?

The first version of this is usually a notebook one person runs by hand. It works until
that person is on holiday.

## Options considered
1. **A notebook or spreadsheet** — fastest, and a single author's tool.
2. **A dashboard on live vendor APIs** — always current, but every view depends on
   every vendor answering, nothing is reproducible, and last quarter is whatever the
   vendor still serves.
3. **A commercial FinOps platform** — the right answer at scale; here the aim is to
   show the mechanics.
4. **Sync into a small local store, and compute reports from it as pure functions.**

## Decision
Option 4.

**Sync, then report.** `finops sync` copies what the vendors and the identity provider
say into one SQLite file. Reports read only that file.
- Writes are upserts keyed by day, vendor, scope and line item: syncing twice changes
  nothing, and a figure a vendor restates is replaced by the newer one.
- A report can be re-run for any past day (`--today`) and gives the same answer.
- A source with no credential is skipped and named, so one missing key doesn't stop the rest.

**Reports are pure functions** of records and config (`finops/reports.py`). No clock, no
network, no database inside them: tests hand them a few records and check the arithmetic.

**Every decision is in one reviewed file,** `config/finops.yaml`: the commitment per
vendor, which team owns which vendor workspace or project, each tier's soft and hard cap,
what a seat costs, and how unusual a day must be to alert. It is validated when loaded
and in CI, so a typo in a team name is an error, not an empty line in a report.

**Spend nobody has claimed is named, not hidden.** A vendor scope that isn't in
`owners:` is reported as unallocated, with its id. A project created outside the process
is exactly the thing this report should find.

**Anomalies use the median and the median absolute deviation** of a team's own last 14
days, not a mean and a standard deviation. One earlier spike then doesn't raise the bar
for the next one. A jump must also be worth at least `min_usd`, so ten dollars where two
is normal is not an alert.

**Projections say what they assume:** mid-month, the projection is the pace so far
extended to month end, and the report says so. A finished month reports what was billed.

**A seat is membership of the identity-provider group that assigns it; using it is
activity the vendor reports.** A seat is a reclaim candidate when it has been held past
a grace period and unused for `inactive_days`. The grace period runs from the first sync
that saw the seat, because the identity provider's group listing doesn't say when
someone was added.

**Alerts are sent once.** Each finding has a key (an anomaly's day, vendor and team; a
team crossing a cap in a month); the store remembers which were sent.

**Credentials are admin keys, in the environment only.** Error messages name the source
and the HTTP status, never the credential. Paging never follows a link to another host.

## Consequences
- Anyone can run it: `finops demo` needs nothing, and the runbook covers the rest.
- The store is a file. One writer at a time; a scheduled job is the intended writer.
- Cost is attributed at the granularity the vendor reports: a workspace or a project.
  Two teams sharing one project can't be told apart here; that needs the gateway's own
  usage log.
- The seat report only knows the products whose activity a vendor exposes per person.
- Projections are linear. A team that spends in bursts will look wrong mid-month.
- **Nothing has been run against a live vendor or Okta.** The adapters follow the
  vendors' documentation and are tested against stand-ins. The first live sync is
  expected to find differences in response shapes.
