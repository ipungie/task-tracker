# Personal Task Tracker

A personal hub built on **Notion** (store + all views) with small Python scripts for the
integrations Notion can't do itself. The scripts run for free on GitHub Actions cron — there is no
server to keep alive.

- **Work** — tasks with deadlines, a calendar/board view, an embedded Google Calendar, and an
  `Events` database for meetings (used via a Calendar view). Task deadlines and Events are pushed
  one-way into Google Calendar by `scripts/notion_to_gcal.py` so they show up with reminders on
  your phone.
- **Health/fitness** — a Workouts database fed from Strava (`scripts/strava_to_notion.py`) and from
  a committed Hevy CSV export (`scripts/hevy_csv_to_notion.py`), a Body Metrics database for weight,
  labs, and vitals (manual entry), and a "📊 Weekly Dashboard" toggle rebuilt at the top of the
  MAIN HUB page each run (`scripts/dashboard_to_notion.py`).

Full rationale for choosing Notion over a custom app / plain-files setup lives in the plan file that
seeded this repo.

## Architecture in one paragraph

Notion is the single source of truth. Each integration is an independent script that only *writes
into* Notion — if the Strava sync breaks, nothing else is affected. Every synced row carries an
`External ID` (e.g. `strava:1234567890`) so re-runs update-or-skip instead of creating duplicates.
Secrets live only in a local untracked `.env` and in GitHub Actions repository secrets, never in the
repo.

## Notion setup (one-time)

1. Create an internal integration at <https://www.notion.so/my-integrations>, copy the token.
2. Create these databases in one workspace and **share each one with the integration** (••• → Add
   connections). `NOTION_TOKEN=... NOTION_PARENT_PAGE=<MAIN HUB page id> python scripts/setup_notion.py`
   creates them for you — it doesn't recreate a database that already exists, but it does add any
   schema property (including the Body Metrics InBody numbers below) that's missing, so it's safe to
   re-run after editing the schema:

   **Tasks** — `Name` (title), `Status` (select: Todo / Doing / Blocked / Done), `Due` (date),
   `Priority` (select), `Project` (select or relation), `Notes` (text). Add a calendar view on
   `Due` and a board view on `Status`.

   **Workouts** — `Name` (title), `Date` (date), `Type` (select: Cardio / Weights / Other),
   `Source` (select: Strava / Hevy / Manual), `Distance` (number), `Duration` (number),
   `Avg HR` (number), `Exercises` (text), `External ID` (text), `Link` (url).
   *Property names must match exactly — the script sets them by name.*

   **Body Metrics** — `Entry` (title), `Date` (date), `Weight` (number), `Body Fat %` (number),
   `Resting HR` (number), `BP` (text), `Lab report` (files), `Notes` (text), plus InBody numbers
   `SMM`, `Body Fat Mass`, `Visceral Fat`, `BMI`, `Waist-Hip Ratio`, `InBody Score`, `BMR`. The
   dashboard shows whichever numbers are filled on a row.

   **Events** — `Name` (title), `When` (date; set a start time and an end time), `Location` (text),
   `Notes` (text). For meetings; `notion_to_gcal.py` pushes each row to Google Calendar.

3. On the **MAIN HUB** page (the dashboard script pins its toggle to the top here):
   - Replace the Google Calendar link/bookmark with a real **`/embed`** block — Google Calendar →
     *Settings* → your calendar → *Integrate calendar* → *Public URL to this calendar* / embed code.
   - Add a **linked view of Tasks** (list or board) with **"+ New"** on, and a **"＋ Task"** button
     block (*Add page to → Tasks*, prefill `Status = Todo`, open the new page). Same for **Workouts**
     (button prefills `Source = Manual`, `Date = @Today`).
   - Add the **Events** database as a **Calendar view** — this is the "add a meeting like in Google
     Calendar" surface: click a day, type a name, set start/end time. You never open the table.
   - Optionally add a **Calendar view of Tasks** on `Due`.
4. Note each database ID — the 32 hex chars in its URL
   (`notion.so/<workspace>/<DATABASE_ID>?v=...`). Put the `Events` id in `.env` as
   `NOTION_EVENTS_DB` (also set `NOTION_TASKS_DB`).

## Local setup

```
cp .env.example .env      # fill in the values
pip install -r requirements.txt
```

## Strava: one-time OAuth

The GitHub app at <https://www.strava.com/settings/api> gives you a Client ID and Client Secret.
You then need a **refresh token** with the `activity:read_all` scope:

1. Open this URL in a browser (replace `CLIENT_ID`):

   ```
   https://www.strava.com/oauth/authorize?client_id=CLIENT_ID&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=activity:read_all
   ```

2. Approve. The browser redirects to `http://localhost/?code=XXXX&...` (the page won't load — that's
   fine). Copy the `code` value.
3. Exchange it for tokens:

   ```
   curl -X POST https://www.strava.com/oauth/token \
     -d client_id=CLIENT_ID \
     -d client_secret=CLIENT_SECRET \
     -d code=XXXX \
     -d grant_type=authorization_code
   ```

4. Put `refresh_token` from the response into `.env` as `STRAVA_REFRESH_TOKEN`. It is long-lived;
   the script trades it for a short-lived access token on every run.

## Google Calendar: one-time setup

`scripts/notion_to_gcal.py` pushes Task deadlines and `Events` rows into a Google Calendar. It
authenticates as a **service account** (no browser OAuth, no token expiry):

1. In the [Google Cloud console](https://console.cloud.google.com) create a project and **enable
   the Google Calendar API**.
2. Create a **service account**, then create a **JSON key** for it and download it.
3. In Google Calendar (web) → the calendar you want to write to → *Settings and sharing* → *Share
   with specific people or groups* → add the service account's email
   (`…@<project>.iam.gserviceaccount.com`) with **"Make changes to events"**.
4. On the same settings page copy the **Calendar ID** (for your primary calendar it's your gmail
   address).
5. Fill `.env`: `GCAL_SA_JSON` = the whole key JSON on one line, `GCAL_CALENDAR_ID` = that id,
   `NOTION_EVENTS_DB` + `NOTION_TASKS_DB` = the database ids.

The sync uses a deterministic Google event id per Notion row (`nt<page-id>`), so re-runs update in
place and nothing is written back to Notion. Only events it created (tagged `notionSync=1`) are
ever modified or deleted — your hand-made calendar events are untouched. One-way only: edits made
in Google Calendar are overwritten on the next run.

## Running the sync

```
python scripts/strava_to_notion.py --selfcheck   # offline sanity check of the pure helpers
python scripts/strava_to_notion.py               # real run
python scripts/hevy_csv_to_notion.py             # import data/hevy.csv (Hevy free export) into Workouts
python scripts/notion_to_gcal.py --selfcheck     # offline check (google libs not needed here)
python scripts/notion_to_gcal.py                 # push Task deadlines + Events to Google Calendar
python scripts/dashboard_to_notion.py            # rebuild the "📊 Weekly Dashboard" at the top of MAIN HUB
```

`strava_to_notion.py` looks at the newest `Source = Strava` row in Workouts and fetches activities
after that date (or the last `STRAVA_BACKFILL_DAYS` days if there are none yet), then creates one
Workout row per new activity. Running it twice in a row must report `0 created` the second time —
that's the duplicate-prevention working.

`dashboard_to_notion.py` reads the Workouts / Body Metrics / Tasks databases and rebuilds a single
toggle block ("📊 Weekly Dashboard"), pinned to the **top** of the MAIN HUB page each run — this
week's training vs targets, latest body metrics vs baseline, and tasks that are overdue / due soon /
high priority / in progress. It only ever touches that one toggle; the Google Calendar `/embed` and
linked views from step 3 of *Notion setup* are left alone (the Notion API can't create those — they
stay a one-time manual paste).

## GitHub Actions (the cron)

Two workflows:

- **`fast.yml`** — every 30 min: `notion_to_gcal.py` + `dashboard_to_notion.py`. Keeps the
  dashboard and calendar fresh (Notion Free can't push events, so this polls). GitHub cron is
  best-effort, so the real interval is ~30–45 min; use *Run workflow* for an instant refresh.
- **`strava-sync.yml`** — every 6h: Strava + Hevy + `notion_to_gcal.py` + dashboard.

Setup:

1. Repo → Settings → Secrets and variables → Actions → add: `NOTION_TOKEN`, `NOTION_WORKOUTS_DB`
   (Strava, optional: `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REFRESH_TOKEN`), and for
   the calendar sync: `NOTION_TASKS_DB`, `NOTION_EVENTS_DB`, `GCAL_SA_JSON`, `GCAL_CALENDAR_ID`.
2. `notion_to_gcal.py` prints `skip` and exits 0 while its secrets are unset, so the workflows go
   green before you finish the Google setup.
3. Actions tab → **fast** (or **strava-sync**) → *Run workflow* to test.

Free-tier Actions minutes (~2000/month on a private repo): `fast.yml` ≈ 1440/month + `strava-sync`
≈ 120. If that gets tight, widen `fast.yml`'s cron to `*/45`, or make the repo public for unlimited
minutes. GitHub also disables scheduled workflows after 60 days with no repo activity.

Secrets are per-repo — if you ever move the repo to another host/account, re-add them there.

## Token rotation

If Strava sync starts failing with 401s, redo *Strava: one-time OAuth* above to get a fresh
`refresh_token`, then update both `.env` and the `STRAVA_REFRESH_TOKEN` GitHub secret.

## Hevy import

Hevy's *API* needs a paid Hevy Pro subscription, so this uses the free export instead: Hevy app →
Settings → Export & Backup Data → save the workouts CSV as `data/hevy.csv` and commit it.
`scripts/hevy_csv_to_notion.py` (in the sync workflow) groups it into one `Source = Hevy` Workout
row per session, keyed by `External ID = hevy:<start_time ISO>` so re-exporting a longer CSV over
the file only adds the new sessions. Missing file = no-op. See CLAUDE.md for the CSV-format notes.
