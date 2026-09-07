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

1. **Work** — tasks with deadlines, a board/calendar view, an embedded Google Calendar. All native
   Notion; no code.
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
- **`setup_notion.py` must stay idempotent** — safe to re-run; it skips any database that already
  exists as a child of the page. (It does *not* yet patch missing properties onto an existing
  database — build-order item, see Current status.)
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
| Quick Log | *planned* — to be created by `setup_notion.py`; ID goes in `.env` as `NOTION_QUICKLOG_DB` |
| Weekly Check-in (page) | *planned* — created during setup; `.env` as `NOTION_SUMMARY_PAGE` |

## Notion schema

- **Tasks**: `Name` (title), `Status` (select: Todo/Doing/Blocked/Done — API can't make a Status-type
  property; convert in the UI if wanted), `Due` (date), `Priority` (select), `Project` (select),
  `Notes` (text).
- **Workouts**: `Name` (title), `Date` (date), `Type` (select: Cardio/Weights/Other), `Source`
  (select: Strava/Hevy/Manual/QuickLog), `Distance` (number, km), `Duration` (number, min),
  `Avg HR` (number), `Exercises` (text), `External ID` (text, the upsert key), `Link` (url).
  **Property names are matched by string in the scripts — don't rename without updating the code.**
- **Body Metrics**: `Entry` (title), `Date`, `Weight`, `Body Fat %`, `Resting HR`, `BP` (text),
  `Lab report` (files — attach the InBody PDF by hand), `Notes`. **Intended** (not yet created by
  `setup_notion.py`): numbers `SMM`, `Body Fat Mass`, `Visceral Fat`, `BMI`, `Waist-Hip Ratio`,
  `InBody Score`, `BMR`. The dashboard already reads them if present (absent ones are skipped) —
  add them in the Notion UI or extend `setup_notion.py`.
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

Runs on the same cron. Rebuilds an at-a-glance summary on the MAIN HUB page
(`be220a6633b8426ab6f88e563d168e8a`) from the three databases. It **owns exactly one block**: a
top-level `toggle` whose title starts with `DASH_MARKER` (`"📊 Weekly Dashboard"`). Each run lists
the page's children, deletes every marker toggle, and re-appends a fresh one via `PATCH
.../children` with an `after` param so it stays in place instead of jumping to the page end. First
run (or if the toggle was deleted/renamed past the marker prefix) appends at the end — drag it once
and subsequent runs keep that position. **Nothing else on the page is touched** — the Google
Calendar `/embed` and any hand-made linked-DB views are safe, and the Notion API can't create those
anyway (still a one-time manual paste, README §3).

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

**Live MAIN HUB layout** (as of first dashboard run, 2026-09-07): headings `Kepentingan Ipung:`
(child pages + a `child_database`) and `Kalender Ipung:` (a **`bookmark` block — not a real
`/embed`**, so it only renders a link card, plus four `child_database` blocks). The dashboard
`toggle` was appended as the **last block, collapsed** — expand it in the UI, or drag it above the
databases once (the `after` logic then keeps it there). Google Calendar still needs the bookmark
replaced with a proper `/embed`.

**Current data state**: Body Metrics DB is **empty** (the InBody seed row, build-order step 2, was
never added) so the Body panel shows "No body metrics logged yet."; no Workouts fall in the last
7 days (newest Hevy session is 2026-08-28) so the training panel reads 0/0; Tasks DB has nothing
matching. All three panels populate once real rows land — the script itself is verified working
(ran twice against live Notion, one toggle, other 13 page blocks untouched).

## Files

Present:

```
scripts/
  setup_notion.py        # one-shot: create the Tasks/Workouts/Body Metrics DBs. Run against MAIN HUB.
  strava_to_notion.py    # Strava OAuth refresh -> activities since watermark -> upsert Workouts
  hevy_csv_to_notion.py  # parse committed data/hevy.csv (Hevy free export) -> upsert Weights Workouts
  dashboard_to_notion.py # rebuild the "📊 Weekly Dashboard" toggle on MAIN HUB (training/body/tasks)
data/
  hevy.csv               # committed Hevy "Export & Backup Data" CSV; re-export over it to add sessions.
.github/workflows/
  strava-sync.yml        # cron every 6h + manual: strava_to_notion.py, hevy_csv_to_notion.py, dashboard_to_notion.py
AGENTS.md                # short pointer to this file + the ponytail/caveman working style
```

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

Planned: `NOTION_QUICKLOG_DB`, `NOTION_SUMMARY_PAGE`.

## Commands

```
pip install -r requirements.txt
python3 scripts/strava_to_notion.py --selfcheck   # offline check of the pure helpers
python3 scripts/<script>.py                        # real run, needs a filled .env
```

No build, no lint, no test framework. Each non-trivial script keeps one `--selfcheck` /
`assert`-based check for its pure logic. Verify real behaviour by running against Notion and
eyeballing the rows; running any sync twice must create nothing the second time.

## Gotchas

- **GitHub Actions free tier ≈ 2000 min/month (private repo).** `strava-sync.yml` is every 6h
  (`0 */6 * * *`) ≈ 120 min/month. Hourly would be ≈ 720; don't schedule below ~30 min.
- **Notion API can't create views, embeds, or linked databases** — the Tasks calendar/board view,
  the Google Calendar embed, and the MAIN HUB linked views are all done by hand in the Notion app.
- **Notion API can't create a `status`-type property** — `Tasks.Status` is a plain select.
- **Notion rate limit ≈ 3 requests/second.** Bulk writers sleep ~0.35 s between calls
  (`# ponytail:` swap for 429-retry if it ever bites).
- **`raw`/CDN staleness** isn't relevant here (no `raw.githubusercontent` reads) — everything goes
  through the Notion API.

## Current status

- `setup_notion.py` created Tasks / Workouts / Body Metrics under MAIN HUB (base props only).
- Committed locally: `setup_notion.py`, `strava_to_notion.py`, `hevy_csv_to_notion.py`,
  `dashboard_to_notion.py`, `strava-sync.yml`, `data/hevy.csv`, `AGENTS.md`. All `--selfcheck`s
  pass; `dashboard_to_notion.py` has been run against live Notion (twice, clean). **Not pushed** —
  the private GitHub repo `ipungie/personal-task-tracker` doesn't exist yet, so CI has never run.
- `strava_to_notion.py` not yet run for real — needs the one-time Strava OAuth (README).
- Remaining per the plan's build order:
  - extend `setup_notion.py`: patch missing props onto existing DBs, add the Body Metrics numbers,
    add the Quick Log DB, add `QuickLog` to `Workouts.Source`.
  - seed the InBody baseline row in Body Metrics (Body panel is empty until then).
  - Weekly Check-in page + `weekly_summary.py` + `weekly-summary.yml`.
  - `quicklog_to_notion.py` + wire it into the sync workflow.
  - Strava OAuth; create the private GitHub repo, push, add Actions secrets, dispatch once.
  - manual Notion polish: Tasks calendar/board views, replace the MAIN HUB Google Calendar
    `bookmark` with a real `/embed`, linked-DB views.
  - rename `strava-sync.yml` -> `sync.yml`.
- The Notion integration token was pasted in a chat once and should be rotated at
  <https://www.notion.so/my-integrations> before the repo is pushed.
