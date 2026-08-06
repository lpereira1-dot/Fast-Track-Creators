# Fast Track Creators

Automates the Fast Track creator gift-card activation program:

1. **Weekly cohort pull** — pulls creators recently added to the Fast
   Track CreatorIQ campaign in weekly cohorts and evaluates gift-card
   eligibility ($25 for a first post within 14 days of being added to the
   campaign, +$25 for a first sale within 14 days).
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
- **"Joined the Fast Track program" means added to a specific Campaign,
  not the overall CreatorIQ network.** `GET /crm/v1/api/campaign/{id}/publishers`
  returns every publisher in that Campaign, keyed by an internal
  `PublisherId`, with `DatePublisherAdded` (when they joined *this*
  campaign) and `ActualPostsTotal` (their current post count). Set
  **`CREATORIQ_CAMPAIGN_ID`** to the CampaignId your Fast Track creators
  are added to — without it, `fetch_new_creators`/`fetch_activation`
  return no data (with a warning), since a creator may join the general
  network long before (or after) being added to this specific campaign.
  The whole roster is fetched once per run and looked up in-memory rather
  than calling per-publisher, since with a few hundred creators added
  recently that both hits CreatorIQ's rate limit (`429`) and is much
  slower — one roster call covered a 283-creator campaign fully in
  testing. Note: this endpoint's `page` param doesn't reliably paginate on
  the account this was tested against (it just returns the same roster
  every time), so the client walks pages defensively and stops once a
  page adds no new publisher ids.
- **Email isn't in the roster, and isn't fetched eagerly.** Getting a
  creator's email requires a 3-hop chain — `GET /publisher/{internal_id}/campaigns`
  (its response `href` happens to contain a *different* "network" id
  scheme CreatorIQ uses for the base publisher resource) → `GET /publisher/{network_id}`
  (gives an `Address.href`) → that href (gives `Email`). Since this is
  expensive per creator, `fetch_new_creators` leaves email blank and
  `CreatorIQClient.fetch_creator_email` is only called lazily, for the
  handful of creators who actually qualify for a gift (see
  `workflow/weekly_job.py`).
- **"First post" has no true CreatorIQ timestamp, so it's tracked locally
  as a proxy.** The roster's `ActualPostsTotal` is a live cumulative count
  with no per-post date attached (and its sibling field,
  `DateRequirementsCompleted`, means "completed posting on *all* required
  platforms" — confirmed on real data to be a much rarer, stricter
  condition than "posted once," so it's not used). Instead,
  `CreatorIQClient.fetch_activation` looks up each creator's current post
  count and hands it to a `FirstPostObserver` (see
  `StateStore.resolve_first_post_dates`), which persists — once, the
  first time a creator's count is seen above zero — the date *our own
  job* observed it, and returns that same stable date on every later
  call. How closely that approximates the true first-post date depends on
  how often the job runs (daily is tighter than weekly).
- **First-sale (and daily GMV) use a real per-transaction date, unlike
  first-post.** `GET /crm/v1/api/ecommerce/transactions?CampaignId={id}`
  (note: NOT the `conversionMetrics` endpoint, which only exposes current
  *cumulative* values with no per-conversion timestamp) returns every
  sale attributed to the campaign via CJ Affiliate (Commission Junction),
  each with a real `TransactionDate`, `Status`, `SaleAmount`, and
  `PublisherId`. This is paginated with `Page`/`PageSize` (default
  `CREATORIQ_TRANSACTIONS_PAGE_SIZE=100`) and a `count` total to page
  through. **`pending` transactions (not yet paid out by the affiliate
  network) count as a qualifying "first sale"** for Fast Track, same as
  `Approved`/`Confirmed` ones — confirmed by the program owner, since a
  sale is a sale even before commission is finalized. Only actively
  declined/reversed transactions (`DeclineReason` set, or a `Status` of
  `declined`/`reversed`/`cancelled`) are excluded — see
  `_is_qualifying_sale` in `src/fast_track/api/creatoriq.py`. The same
  transactions feed daily GMV/sales-count for the retention dashboard via
  `fetch_activity` (posts aren't included there, since there's still no
  per-day post-count history — see the first-post point above).
- **Field names** — `src/fast_track/api/field_mapper.py` lists the
  candidate field names tried for each normalized attribute (creator id,
  email, joined date, etc). If your account's payload uses a field name
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
| Days to complete a milestone after being added to the Fast Track campaign | `ACTIVATION_WINDOW_DAYS` | 14 |
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

### Deploying the dashboard so there's a real, shareable link

Running it locally (`fast-track dashboard`) only gives you a `localhost`
URL. To get an actual link your team can open, deploy it to
[Streamlit Community Cloud](https://share.streamlit.io) (free):

1. Go to share.streamlit.io → **New app** → pick this repo/branch and set
   the main file path to `src/fast_track/dashboard/app.py`.
2. Before (or after) deploying, open the app's **Settings → Secrets** and
   paste in values based on `.streamlit/secrets.toml.example` (copy that
   file's contents, fill in real values — `CREATORIQ_API_KEY`,
   `CREATORIQ_CAMPAIGN_ID`, `GIFT_ORDER_SHEET_ID`,
   `GOOGLE_SERVICE_ACCOUNT_JSON`, etc). `app.py` bridges these into regular
   environment variables at startup, so nothing else needs to change.
3. Also set `GITHUB_REPO` (`"owner/repo"`) and a `GITHUB_TOKEN` (a
   fine-grained PAT with **Actions: Read-only** access to this repo). This
   is the important one: Streamlit Cloud's filesystem is completely
   separate from wherever `run-weekly-job`/`sync-activity` actually run
   (GitHub Actions) — without it, the dashboard would just show "No gift
   awards recorded yet" forever. With it, the dashboard downloads the
   latest `fast-track-db` artifact those workflows already upload on every
   run (see `src/fast_track/dashboard/db_sync.py`) each time it loads.

Any other host that runs a `streamlit run` command works too (Render,
Railway, Fly.io, a small VM, etc.) — the app doesn't require Streamlit
Cloud specifically, just something to keep a Python process alive and
reachable over HTTP; `requirements.txt` at the repo root covers dependency
installation for platforms that don't recognize `pyproject.toml` directly.

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
