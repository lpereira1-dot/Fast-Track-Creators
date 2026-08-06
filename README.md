# Fast Track Creators

Automates the Fast Track creator gift-card activation program:

1. **Weekly cohort pull** — pulls newly-joined creators from **CreatorIQ**
   in weekly cohorts and evaluates gift-card eligibility ($25 for a first
   post within 14 days of joining, +$25 for a first sale within 14 days).
2. **Gift-card ordering sheet sync** — appends newly-qualified creators
   (name + email + milestone) to the existing Google Sheet your ordering
   team already uses, so gift cards get ordered without anyone manually
   pulling reports.
3. **Retention dashboard** — a Streamlit dashboard comparing each gifted
   creator's activity for 30 days before vs. after they received a gift
   card, so you can see the lift/retention the incentive drove.

Everything is idempotent and safe to re-run: a creator's milestone is only
ever written to the sheet once, tracked both locally and by checking the
sheet itself.

## How it fits together

```
CreatorIQ API                 fast-track run-weekly-job (weekly, e.g. every Monday)
 (publishers,       ────►     1. Pull creators who joined in the last ~3 weeks
  activation,                 2. Evaluate $25 first-post / $25 first-sale eligibility
  activity reports)           3. Append newly-qualified creators to the Google Sheet
                               4. Record what was sent, so re-runs never duplicate

                               fast-track sync-activity (daily)
                               Refreshes each known creator's daily posts/sales/GMV
                               so the dashboard's pre/post windows stay current

Local state (SQLite)  ────►   fast-track dashboard (Streamlit)
 creators, gift awards,       Pre/post 30-day activity + retention curve,
 daily activity history       cohort breakdown, per-creator drill-down
```

## Quickstart (demo mode, no credentials needed)

This repo ships with realistic fixture data (`fixtures/creatoriq/`) shaped
like CreatorIQ API responses, so you can try the whole pipeline end-to-end
before wiring up real credentials.

```bash
pip install -e ".[dev]"

# 1. Import the demo program's historical creators/awards for the dashboard
#    (this does NOT write to any Google Sheet -- see "Backfilling history" below).
CREATORIQ_USE_FIXTURES=true fast-track backfill --since 2026-05-01 --until 2026-08-05

# 2. Pull daily activity so the retention dashboard has data to show.
CREATORIQ_USE_FIXTURES=true fast-track sync-activity

# 3. See what the weekly job would do next (dry run, prints instead of writing to a sheet).
CREATORIQ_USE_FIXTURES=true fast-track run-weekly-job --dry-run

# 4. Launch the dashboard.
fast-track dashboard
```

## Setting up with real CreatorIQ + Google Sheets credentials

1. Copy `.env.example` to `.env` and fill in:
   - `CREATORIQ_BASE_URL` / `CREATORIQ_API_KEY` — request an API key from
     your CreatorIQ account rep or `support@creatoriq.com` (ExchangeIQ API,
     auth via a static `x-api-key` header).
   - `GIFT_ORDER_SHEET_ID` — the spreadsheet ID (from its URL) of the
     ordering team's existing Google Sheet.
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — a
     [Google service account](https://cloud.google.com/iam/docs/service-account-overview)
     JSON key with the Sheets API enabled, given either as a file path or as
     the raw JSON key contents directly (useful when this is set via a
     secrets manager / CI / Cursor Cloud secret rather than a local file).
     **Share the target spreadsheet with the service account's
     `client_email`** (Editor access) so it can append rows.
2. Run `fast-track run-weekly-job --dry-run` first to sanity-check what it
   would add, then drop `--dry-run` to actually sync.
3. Schedule it — see [Scheduling](#scheduling) below.

### Adapting to your CreatorIQ account

CreatorIQ's exact endpoint shapes and JSON field names can vary by account
and API version (the full reference lives behind a login at
[apidocs.creatoriq.com](https://apidocs.creatoriq.com)). Rather than
hard-code one schema, this integration is deliberately configurable — and
the defaults below have been **confirmed against a real CreatorIQ (Wayfair)
account**, which surfaced a few things worth knowing:

- **The real API is namespaced under `/crm/v1/api/...`**, not a generic
  `/v1/...` REST tree. Any request outside that prefix (wrong path, wrong
  auth, doesn't matter) gets an identical generic `403 {"message":"Forbidden"}`
  from CreatorIQ's edge — which looks like a credentials/IP problem but
  usually just means the path is wrong. If you see this, double-check the
  path is under `/crm/v1/api/`.
- **List endpoints are async "view" reports, not simple REST lists.**
  Fetching new creators works like: `GET {CREATORIQ_VIEW_PATH}?view={CREATORIQ_PUBLISHERS_VIEW}&requestData[take]=...&requestData[skip]=...`
  creates a task; the client polls until `TaskStatus` is `DONE`, then
  fetches the actual rows from the signed URL in `Result.Headers.Location`.
  See `CreatorIQClient._run_view_report` in `src/fast_track/api/creatoriq.py`.
  The default view, `Reports/Publishers`, conveniently returns `PublisherId`,
  `PublisherName`, `Email`, and a clean ISO `RecruitingStarted` join-date
  directly — no secondary lookups needed for the new-creators pull.
- **Pagination is `take`/`skip`, sorted descending.** New creators are
  fetched newest-first (`CREATORIQ_PUBLISHERS_VIEW_SORT_FIELD`, default
  `RecruitingStarted`) so the client can stop as soon as it pages past the
  lookback window, rather than scanning the entire publisher list (large
  accounts can have 900k+ publishers total).
- **First-post completion comes from campaign membership, not an
  "activation report" endpoint.** `GET /crm/v1/api/publisher/{id}/campaigns`
  returns each campaign a publisher belongs to, and a membership's
  `DateRequirementsCompleted` field is set once they've fulfilled that
  campaign's post requirements. Set **`CREATORIQ_CAMPAIGN_ID`** to the
  CampaignId your Fast Track creators are added to/required to post
  for — without it, `fetch_activation` returns no data (with a warning) since
  a creator may belong to many unrelated campaigns and there's no way to
  know which membership matters.
- **First-sale / daily activity (GMV) is not wired up yet.** The CRM API's
  `GET /crm/v1/api/campaign/{campaignId}/publisher/{publisherId}/conversionMetrics`
  endpoint exists, but only exposes *current cumulative* values (e.g.
  total orders, total GMV) with no per-conversion timestamp — so it can't
  directly answer "when did this creator's first sale happen." Likely
  options, not yet implemented: (a) poll this endpoint daily and treat the
  date a metric is first observed going from zero to non-zero as the
  "first sale" date (requires local state to track previous values), or
  (b) if your program's sales are attributed via CreatorIQ's separate
  Link-Tracking API (a different host/API from ExchangeIQ), use its
  per-click/conversion event log instead, if it has dates.
- **Field names** — `src/fast_track/api/field_mapper.py` lists the
  candidate field names tried for each normalized attribute (creator id,
  email, first post date, etc). If your account's payload uses a field name
  not already in that list, add it there — no other code needs to change.

Once you have a real API response in hand, the fastest way to verify the
mapping is correct is to drop a sample payload into `fixtures/creatoriq/`
(matching the shape of `publishers.json` / `activation.json` /
`activity.json`) and run `CREATORIQ_USE_FIXTURES=true fast-track run-weekly-job --dry-run`
(or, against live credentials, just `fast-track run-weekly-job --dry-run`).

### Backfilling history from the manual test period

Since this program's first cohorts were already run manually (gift cards
hand-ordered from daily-pulled reports), you don't want the automated job
re-adding those same creators to the ordering sheet. Use `fast-track
backfill` instead of `run-weekly-job` for that historical window — it
populates local state (creators + awards) for the dashboard **without ever
writing to the Google Sheet**:

```bash
fast-track backfill --since 2026-05-01 --until 2026-06-01
fast-track sync-activity
```

Going forward, `fast-track run-weekly-job` (scheduled weekly) picks up
where the manual process left off and keeps the sheet in sync automatically.

## Program rules

| Rule | Env var | Default |
|---|---|---|
| Days to complete a milestone after joining | `ACTIVATION_WINDOW_DAYS` | 14 |
| First-post gift amount | `FIRST_POST_GIFT_USD` | $25 |
| First-sale gift amount | `FIRST_SALE_GIFT_USD` | $25 |
| Retention dashboard pre/post window | `RETENTION_WINDOW_DAYS` | 30 days |
| Cohort week start (ISO weekday, 1=Mon) | `COHORT_WEEK_START_WEEKDAY` | 1 (Monday) |

A creator can earn both gifts independently and at different times (e.g.
posts on day 2, sells on day 12) — the weekly job re-checks creators for a
rolling ~3-week window (activation window + 1 week buffer) specifically so
a milestone hit in a later week than the join week still gets caught.

## Google Sheet layout

The weekly job only ever **appends rows** — it never reformats or reorders
existing columns — using this header (customize via
`GIFT_ORDER_SHEET_COLUMNS`, comma-separated):

`Creator ID, Creator Name, Email, Milestone, Gift Amount (USD), Joined At, Milestone Completed At, Cohort Week, Added At, Status`

If the named worksheet tab doesn't exist yet, it's created automatically
with this header row.

## The dashboard

Run with `fast-track dashboard` (or `streamlit run src/fast_track/dashboard/app.py`).

For every creator who earned a gift, their "gift date" is the date of their
earliest qualifying milestone. The dashboard shows, for the configurable
retention window (default 30 days) before/after that date:

- **Summary cards**: pre- vs. post-gift active rate, activity lift %, a
  "final week" retention rate (% still active in the last week of the
  post-gift window — a proxy for durable retention rather than a one-time
  activity spike), and total gift spend.
- **Retention curve**: % of creators active each day, from -N to +N days
  relative to the gift.
- **Cohort breakdown**: gifted creators & spend per weekly join cohort.
- **Creator detail table**: per-creator pre/post posts, sales, and active days.

Filters in the sidebar let you slice by milestone type, cohort week, and
adjust the retention window on the fly.

## Scheduling

Two GitHub Actions workflows are included:

- [`weekly-gift-cohort.yml`](.github/workflows/weekly-gift-cohort.yml) —
  runs `fast-track run-weekly-job` every Monday.
- [`daily-activity-sync.yml`](.github/workflows/daily-activity-sync.yml) —
  runs `fast-track sync-activity` daily to keep the dashboard current.

Required repository secrets: `CREATORIQ_BASE_URL`, `CREATORIQ_API_KEY`,
`CREATORIQ_CAMPAIGN_ID` (optional), `GIFT_ORDER_SHEET_ID`,
`GOOGLE_SERVICE_ACCOUNT_JSON` (the full JSON key contents, pasted as a secret).

> **Persistence note:** these workflows cache `data/fast_track.db` via
> `actions/cache`, which is convenient but not guaranteed-durable long-term
> storage (GitHub evicts caches unused for 7+ days). The Google Sheet itself
> is a second line of defense against duplicate gift-card rows even if that
> cache is lost, but the *dashboard's* activity history would need
> re-backfilling. For a more durable setup, run the same CLI commands from
> a small persistent host/VM via cron (pointing `FAST_TRACK_DB_PATH` at a
> real disk), or swap `StateStore` for a hosted database.

## Local development

```bash
pip install -e ".[dev]"
pytest                 # unit + integration tests (all run against fixtures/mocks, no live API calls)
ruff check .           # lint
python scripts/generate_fixtures.py   # regenerate the demo dataset (deterministic)
```

## Project layout

```
src/fast_track/
  config.py                 Env-var-driven settings (CreatorIQ, Sheets, program rules, storage)
  models.py                 Creator, ActivationRecord, ActivityRecord, GiftAward, Milestone
  api/
    creatoriq.py             CreatorIQClient (live) + FixtureCreatorIQClient (demo/tests)
    field_mapper.py           Tolerant field-name lookup for CreatorIQ's JSON payloads
  workflow/
    cohorts.py                Weekly cohort bucketing
    eligibility.py             $25/$25 gift-card eligibility rules
    weekly_job.py               Orchestrates the weekly pull -> eligibility -> sheet sync
    activity_sync.py             Refreshes daily activity history for the dashboard
    backfill.py                   One-time historical import (dashboard only, no sheet writes)
  sheets/
    gift_order_sheet.py        Idempotent Google Sheets append client
  storage/
    state_store.py              SQLite: creators, recorded gift awards, daily activity
  dashboard/
    metrics.py                   Pre/post retention math (pandas, independently testable)
    app.py                        Streamlit UI
  cli.py                       `fast-track` command-line entrypoint
fixtures/creatoriq/           Sample CreatorIQ-shaped JSON payloads for demos/tests
scripts/generate_fixtures.py  Regenerates the fixtures above
tests/                        pytest suite (cohorts, eligibility, sheet idempotency, metrics, ...)
.github/workflows/            Scheduled GitHub Actions for the weekly + daily jobs
```
