# CLAUDE.md

Guidance for Claude Code working in this repo. `AGENTS.md` is a short pointer back here for other
agents; this file stays the source of truth.

**Working style:** this repo runs the `ponytail` (laziest solution that works) and `caveman` (terse
chat output) skills — apply both to every change. Prose in code, comments, commits, and docs stays
normal English.

## What this is

A personal life hub for @ipungie. **Notion is the datastore and the entire view layer.** This repo
holds only the integration glue Notion can't do itself: small Python scripts that run on GitHub
Actions cron (free, stateless, no server). No framework, no bundler, no web app.

Two domains:

1. **Work** — tasks with deadlines, a board/calendar view, an embedded Google Calendar, and an
   `Events` DB for meetings. Task deadlines and Events are pushed one-way to Google Calendar by
   `notion_to_gcal.py` so they nag on the phone; everything else here is native Notion, no code.
2. **Health/fitness** — body composition from InBody scans (manual entry), cardio auto-pulled from
   Strava, lifts from a committed Hevy CSV export (and, planned, a typed Quick Log parsed by
   regex), and a weekly check-in that compares logged activity to fixed targets. **No calorie
   math** — TDEE/burn estimates are ±20% noise; correction comes from the next InBody, not
   arithmetic.

Full rationale and the rejected alternatives (custom app, git+markdown, Obsidian) are in the plan
file that seeded this repo: `~/.claude/plans/ancient-baking-pony.md`.

## Architectural rules (do not violate without a good reason)

- **Notion is the single source of truth.** Scripts only *write into* it; they never hold state
  elsewhere.
- **One Notion database per domain. Each integration is an independent script.** The Strava sync
  breaking must never affect Tasks or Quick Log. A new domain = a new database + optionally a new
  script, not a change to an existing one.
- **Every synced Workout row carries an `External ID`** so re-runs upsert instead of duplicating:
  `strava:<activity_id>` for Strava, `quicklog:<row_id>:<line#>` for Quick Log. A script must query
  by `External ID` before creating.
- **Secrets never in the repo.** `.env` is gitignored; `.env.example` documents the names; real
  values live in GitHub Actions repository secrets.
- **`setup_notion.py` must stay idempotent** — safe to re-run; it doesn't recreate a database that
  already exists as a child of the page, and it adds any schema property that database is missing
  (add a prop to `SCHEMAS`, re-run, done). It never renames or retypes an existing property.
- Prefer the laziest thing that works (this repo runs the `ponytail` + `caveman` skills). Regex over
  an LLM, a Notion embed over a sync, cron over a webhook. Mark deliberate corners with a
  `# ponytail:` comment naming the ceiling.

## Notion IDs

Parent page **MAIN HUB**: `be220a6633b8426ab6f88e563d168e8a`
(the integration is called "Task Tracker"; the page must be shared with it).

| Database / page | ID |
|---|---|
| Tasks | `3d4fdaa633b081638123f1307cd1f7af` |
| Workouts | `3d4fdaa633b081cfbc6ed0d096baa224` |
| Body Metrics | `3d4fdaa633b081bc8e1ac8e607ead38a` |
| Events | `3d4fdaa633b08151b6daea9717834fae` (created 2026-09-07; set as `NOTION_EVENTS_DB`) |
| Quick Log | *planned* — to be created by `setup_notion.py`; ID goes in `.env` as `NOTION_QUICKLOG_DB` |
| Weekly Check-in (page) | *planned* — created during setup; `.env` as `NOTION_SUMMARY_PAGE` |

## Notion schema

- **Tasks**: `Name` (title), `Status` (select: Todo/Doing/Blocked/Done — API can't make a Status-type
  property; convert in the UI if wanted), `Due` (date), `Priority` (select), `Project` (select),
  `Notes` (text).
- **Events**: `Name` (title), `When` (date — set a start time, and an end time; a date with no time
  becomes an all-day calendar event), `Location` (text), `Notes` (text). Meant to be used via a
  Calendar view in Notion (no table). `notion_to_gcal.py` pushes each row to Google Calendar.
- **Workouts**: `Name` (title), `Date` (date), `Type` (select: Cardio/Weights/Other), `Source`
  (select: Strava/Hevy/Manual/QuickLog), `Distance` (number, km), `Duration` (number, min),
  `Avg HR` (number), `Exercises` (text), `External ID` (text, the upsert key), `Link` (url).
  **Property names are matched by string in the scripts — don't rename without updating the code.**
- **Body Metrics**: `Entry` (title), `Date`, `Weight`, `Body Fat %`, `Resting HR`, `BP` (text),
  `Lab report` (files — attach the InBody PDF by hand), `Notes`, plus numbers `SMM`,
  `Body Fat Mass`, `Visceral Fat`, `BMI`, `Waist-Hip Ratio`, `InBody Score`, `BMR` (all created by
  `setup_notion.py`; the dashboard shows whichever are present on a row).
- **Quick Log**: `Note` (title — the typed line), `Date` (date, default today), `Parsed` (checkbox),
  `Result` (text — parser summary or error).

## Quick Log format (parsed by `quicklog_to_notion.py`) — NOT BUILT YET

Design intent for a future `quicklog_to_notion.py`; no script, DB, or workflow step exists yet.

One session per row. Lines separated by comma or newline. Optional leading `YYYY-MM-DD:` overrides
the row's `Date`.

- **Strength**: `exercise SxRxW` — `bench 4x8x60`, `incline db press 3x12x24`. All lift lines in a
  row collapse into one `Type = Weights` Workout row; `Exercises` gets the normalised form
  `Bench 4x8 @60kg; Incline DB Press 3x12 @24kg`.
- **Cardio**: `verb Dk MM:SS` (`run 5k 28:00`) or `verb N min` (`bike 40min`). One `Type = Cardio`
  Workout row with `Distance` (km) and `Duration` (min).

Parser behaviour: process every line it understands; only leave `Parsed` unchecked if **zero**
entries were created; always write what happened (or why not) to `Result`.

## Hevy import (`hevy_csv_to_notion.py`)

Bulk lift logging without typing every set into Quick Log — and without Hevy Pro (the Hevy *API*
needs it; the free CSV doesn't). Flow: in the Hevy app, Settings → Export & Backup Data → save the
workouts CSV as `data/hevy.csv`, commit it. The script (in `strava-sync.yml`, every 6h) reads that file —
one row per set — groups rows into sessions by `start_time`, and writes one `Type = Weights`,
`Source = Hevy` Workout row per session. `Exercises` uses the same normalised shape as Quick Log
(`Squat 4x5 @100kg; Bench 8@60,8@60,7@62.5kg` when sets vary); warm-up sets are dropped; timed
holds (blank reps) are skipped from the summary. `External ID = hevy:<start_time ISO>` is the
upsert key, so re-exporting a longer CSV over the file only adds the new sessions
(`latest_hevy_start()` skips anything at/before the newest synced date; `already_synced()` is the
second guard). Missing `data/hevy.csv` = no-op. Quick Log stays for quick one-offs.
Hevy's CSV date format varies by app version — `parse_dt()` covers the common ones; add a format
there if a real export doesn't parse (`--selfcheck` exercises the pure helpers).

## MAIN HUB dashboard (`dashboard_to_notion.py`)

Runs on both crons (`fast.yml` every 30 min, `strava-sync.yml` every 6h). Rebuilds an at-a-glance
summary on the MAIN HUB page (`be220a6633b8426ab6f88e563d168e8a`) from the three databases. It
**owns exactly one block**: a top-level `toggle` whose title starts with `DASH_MARKER`
(`"📊 Weekly Dashboard"`). Each run lists the page's children, deletes every marker toggle, and
re-appends a fresh one **pinned to the top** via `PATCH .../children` with
`position: {"type": "start"}` — which needs `Notion-Version: 2026-03-11`, sent on that one request
only (the rest of the script stays on `2022-06-28`; the `2025-09-03`+ data-source model changes
DB-query semantics). **Nothing else on the page is touched** — the Google Calendar `/embed` and any
hand-made linked-DB views are safe, and the Notion API can't create those anyway (still a one-time
manual paste, README §3).

Three panels, built by pure functions (parsed rows in, block dicts out — `--selfcheck` covers them):

- **🏋️ This week** — Workouts with `Date` in the last 7 days: count `Type = Weights` rows vs
  `LIFT_TARGET` (2), sum `Duration` of `Type = Cardio` vs `CARDIO_MIN_TARGET` (200). "n / target — k
  to go". Constants at the top of the file.
- **📊 Body** — Body Metrics sorted by `Date`: `oldest → latest  (±delta)` for every numeric
  property present (preferred order first, then any extras). One row → values only. No rows → "none
  yet". `# ponytail:` baseline is the oldest row; switch to previous-row if InBody scans get
  frequent.
- **✅ Needs attention (N)** — Tasks with `Status ≠ Done` that are overdue, due within 7 days,
  `Priority = High`, or `Status = Doing`. One `to_do` (unchecked) each: a page-mention chip +
  ` — <due> · <priority>`, `⚠️ ` prefix when overdue. Overdue first, then soonest due; capped at 15
  with a "+ N more" line. `select_name()` reads both `select` and `status` property types, so
  converting `Tasks.Status` to a real Status type in the UI won't break it.

DB/page IDs are baked in as constants (they're already public in this file / README); only
`NOTION_TOKEN` is required. `NOTION_MAIN_HUB` / `NOTION_TASKS_DB` / `NOTION_WORKOUTS_DB` /
`NOTION_BODY_DB` override them. A DB that 404s → "share it with the integration".

**Live MAIN HUB layout** (2026-09-07): headings `Kepentingan Ipung:` (child pages + a
`child_database`) and `Kalender Ipung:` (a **`bookmark` block — not a real `/embed`**, so it only
renders a link card, plus four `child_database` blocks). Since the `position: {"type": "start"}`
change the dashboard `toggle` is re-created as the **first block** on the page every run. Google
Calendar still needs the bookmark replaced with a proper `/embed`, and the quick-add views/buttons
+ the `Events` Calendar view are still a manual paste (README).

**Current data state**: Body Metrics has **one row** — the InBody scan from 2026-07-07 (Urban Gym
Bandung: Weight 96.3, BF% 31.0, SMM 38.1, BFM 29.9, Visceral Fat 12, BMI 31.4, WHR 0.98, InBody
Score 71, BMR 1804); with a single row the Body panel shows values only (no delta), and the scan
PDF still needs attaching to `Lab report` by hand. No Workouts fall in the last 7 days (newest
Hevy session is 2026-08-28) so the training panel reads 0/0; Tasks has 1 row flagged. The script
is verified working against live Notion (one toggle, other page blocks untouched).

## Notion → Google Calendar (`notion_to_gcal.py`)

One-way push (Notion is still the source of truth). Runs on both crons. Two sources:

- **Tasks** with a `Due` date and `Status ≠ Done` → an **all-day** event on the due date,
  `summary = "📋 <name>"`, `transparency = transparent` (doesn't show as busy), `visibility =
  private`, one `popup` reminder at `TASK_REMINDER_MIN` (540 = 09:00 local; `# ponytail:` fixed).
- **Events** rows with a `When` value → a **timed** event (`start.dateTime` + `timeZone`, default
  `GCAL_TZ = Asia/Jakarta`; end = `When`'s end or start + 1 h), `location` from `Location`,
  calendar-default reminders. A `When` with no time → all-day.

**Idempotency without write-back:** the Google event id is derived from the Notion page id —
`event_id() = "nt" + <page id hex, 32 chars>` (valid Google id: a-v + digits). Upsert =
`events.insert(id=…)`, and on `409` (id exists) → `events.patch`. Every event carries
`extendedProperties.private.notionSync = "1"`. **Delete reconciliation:** once per run, list only
`privateExtendedProperty=notionSync=1` events in a `[-7 d, +400 d]` window; any whose id isn't in
the current live set (row deleted, marked Done, date cleared) gets `events.delete`, swallowing
404/410. Hand-made calendar events (no `notionSync` tag) are never listed or touched.

Auth: a Google Cloud **service account** JSON in `GCAL_SA_JSON`; the target calendar
(`GCAL_CALENDAR_ID`, never `"primary"`) must be shared with the SA email as "Make changes to
events". No OAuth dance, no token expiry. `google-api-python-client` + `google-auth` are imported
lazily inside the Google-I/O functions so `--selfcheck` runs without the dependency. Prints
`skip: not configured yet (…)` and exits 0 if the `GCAL_*` / `NOTION_EVENTS_DB` secrets are unset.

## Files

Present:

```
scripts/
  notion_common.py       # shared Notion helpers: env(), notion_headers(version=None), notion_query() paginator
  setup_notion.py        # create the Tasks/Workouts/Body Metrics/Events DBs + add any missing schema props. Run against MAIN HUB.
  strava_to_notion.py    # Strava OAuth refresh -> activities since watermark -> upsert Workouts
  hevy_csv_to_notion.py  # parse committed data/hevy.csv (Hevy free export) -> upsert Weights Workouts
  dashboard_to_notion.py # rebuild the "📊 Weekly Dashboard" toggle, pinned to the top of MAIN HUB
  notion_to_gcal.py      # one-way push: Task deadlines + Events -> Google Calendar (deterministic event ids)
data/
  hevy.csv               # committed Hevy "Export & Backup Data" CSV; re-export over it to add sessions.
.github/workflows/
  fast.yml               # cron every 30 min + manual: notion_to_gcal.py, dashboard_to_notion.py
  strava-sync.yml        # cron every 6h + manual: strava + hevy + notion_to_gcal + dashboard
AGENTS.md                # short pointer to this file + the ponytail/caveman working style
```

`scripts/` is not a package; running `python scripts/<x>.py` puts that dir on `sys.path`, so
`from notion_common import ...` resolves. `strava_to_notion.py`, `hevy_csv_to_notion.py`,
`dashboard_to_notion.py`, `setup_notion.py`, `notion_to_gcal.py` all import from it (the
`# ponytail:` "4th script" trigger fired).

Planned (per Current status): `scripts/quicklog_to_notion.py` (Quick Log rows -> Workout rows,
regex), `scripts/weekly_summary.py` (last-7-day Workouts vs targets + weight delta -> Weekly
Check-in page), and a `weekly-summary.yml` workflow. `strava-sync.yml` was to be renamed `sync.yml`.

`dashboard_to_notion.py` (and the planned `weekly_summary.py`) share the same targets, constants at
the top of each file: `CARDIO_MIN_TARGET = 200`, `LIFT_TARGET = 2`.

## Env vars

In use now: `NOTION_TOKEN`, `NOTION_WORKOUTS_DB`, `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`,
`STRAVA_REFRESH_TOKEN`, optional `STRAVA_BACKFILL_DAYS`, optional `HEVY_CSV_PATH` (default
`data/hevy.csv`), optional dashboard overrides `NOTION_MAIN_HUB` / `NOTION_TASKS_DB` /
`NOTION_BODY_DB` (default to the IDs above). `setup_notion.py` uses `NOTION_TOKEN` +
`NOTION_PARENT_PAGE`.

`notion_to_gcal.py`: `NOTION_TOKEN`, `NOTION_EVENTS_DB` (required — from `setup_notion.py`),
`GCAL_SA_JSON` (service-account key JSON, one line), `GCAL_CALENDAR_ID` (calendar id, e.g. the
gmail address), optional `NOTION_TASKS_DB` (baked default), optional `GCAL_TZ` (default
`Asia/Jakarta`). Missing any of the three required → the script prints `skip` and exits 0.

Planned: `NOTION_QUICKLOG_DB`, `NOTION_SUMMARY_PAGE`.

## Commands

```
pip install -r requirements.txt                    # requests + google-api-python-client + google-auth
python3 scripts/strava_to_notion.py --selfcheck    # offline check of the pure helpers
python3 scripts/notion_to_gcal.py --selfcheck      # ditto; google libs not needed for --selfcheck
python3 scripts/<script>.py                         # real run, needs a filled .env
```

No build, no lint, no test framework. Each non-trivial script keeps one `--selfcheck` /
`assert`-based check for its pure logic. Verify real behaviour by running against Notion and
eyeballing the rows; running any sync twice must create nothing the second time.

## Gotchas

- **GitHub Actions free tier ≈ 2000 min/month (private repo).** `fast.yml` every 30 min
  (`*/30 * * * *`) ≈ 1440 min/month + `strava-sync.yml` every 6h ≈ 120 → ~1560, ~440 headroom.
  The `pip` cache (`actions/setup-python` `cache: pip`) keeps each `fast` run ~1 min — don't drop
  it. If minutes bite: widen `fast.yml` to `*/45`, or make the repo public (unlimited Actions
  minutes) and go tighter.
- **GitHub disables scheduled workflows after 60 days of no repo activity**, and cron is
  best-effort — a `*/30` schedule really fires every ~30–45 min under load. The manual
  "Run workflow" button on `fast.yml` is the instant-refresh escape hatch.
- **Notion API can't create views, embeds, or linked databases** — the Tasks calendar/board view,
  the Google Calendar embed, and the MAIN HUB linked views are all done by hand in the Notion app.
- **Notion API can't create a `status`-type property** — `Tasks.Status` is a plain select.
- **Notion rate limit ≈ 3 requests/second.** Bulk writers sleep ~0.35 s between calls
  (`# ponytail:` swap for 429-retry if it ever bites).
- **`raw`/CDN staleness** isn't relevant here (no `raw.githubusercontent` reads) — everything goes
  through the Notion API.

## Current status

- Pushed to `github.com/ipungie/task-tracker` (private). `strava-sync.yml` runs on Actions every
  6h; a manual dispatch on 2026-09-07 went green (Strava step 400s and is skipped via
  `continue-on-error`; Hevy + dashboard steps pass).
- Actions secrets set: `NOTION_TOKEN`, `NOTION_WORKOUTS_DB`. Strava secrets not set (no API access).
- `setup_notion.py` created Tasks / Workouts / Body Metrics under MAIN HUB and has since added the
  7 InBody number props to Body Metrics. One InBody row (2026-07-07) is in Body Metrics.
- `strava_to_notion.py` never run for real — needs the one-time Strava OAuth (README) if access
  is ever obtained.
- **Calendar feature — code landed, not yet wired up.** `notion_common.py`, `notion_to_gcal.py`,
  `fast.yml`, the `Events` schema, and the google deps are committed; all `--selfcheck`s pass. Not
  yet done: run `setup_notion.py` to create the `Events` DB; the Google Cloud project + service
  account + calendar share; the `NOTION_EVENTS_DB` / `NOTION_TASKS_DB` / `GCAL_SA_JSON` /
  `GCAL_CALENDAR_ID` Actions secrets; the manual MAIN HUB views (quick-add Tasks/Workouts, `Events`
  Calendar view) and the real Google Calendar `/embed`. Until the secrets exist `notion_to_gcal.py`
  prints `skip` and the workflows stay green.
- Remaining per the plan's build order:
  - add the Quick Log DB to `setup_notion.py` (+ `QuickLog` to `Workouts.Source`).
  - Weekly Check-in page + `weekly_summary.py` + `weekly-summary.yml`.
  - `quicklog_to_notion.py` + wire it into the sync workflow.
  - attach the InBody PDF to `Lab report` by hand.
  - rename `strava-sync.yml` -> `sync.yml` (and drop or keep the parked Strava step).
- The Notion integration token was pasted in a chat once — rotate it at
  <https://www.notion.so/my-integrations> and update `.env` + the `NOTION_TOKEN` Actions secret.
