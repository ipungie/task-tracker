# Personal Task Tracker

A personal hub built on **Notion** (store + all views) with small Python scripts for the
integrations Notion can't do itself. The scripts run for free on GitHub Actions cron — there is no
server to keep alive.

- **Work** — tasks with deadlines, a calendar/board view, and an embedded Google Calendar, all in
  Notion.
- **Health/fitness** — a Workouts database fed from Strava (via `scripts/strava_to_notion.py`), plus
  a Body Metrics database for weight, labs, and vitals (manual entry).

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
   connections):

   **Tasks** — `Name` (title), `Status` (select: Todo / Doing / Blocked / Done), `Due` (date),
   `Priority` (select), `Project` (select or relation), `Notes` (text). Add a calendar view on
   `Due` and a board view on `Status`.

   **Workouts** — `Name` (title), `Date` (date), `Type` (select: Cardio / Weights / Other),
   `Source` (select: Strava / Hevy / Manual), `Distance` (number), `Duration` (number),
   `Avg HR` (number), `Exercises` (text), `External ID` (text), `Link` (url).
   *Property names must match exactly — the script sets them by name.*

   **Body Metrics** — `Date` (date), `Weight` (number), `Body Fat %` (number),
   `Resting HR` (number), `BP` (text), `Lab report` (files), `Notes` (text).

3. Create a **Dashboard** page. Add a `/embed` block with your Google Calendar's secret embed URL
   (Google Calendar → Settings → your calendar → *Integrate calendar* → *Public URL to this
   calendar* / embed code). Add linked views of the databases above.
4. Note each database ID — the 32 hex chars in its URL
   (`notion.so/<workspace>/<DATABASE_ID>?v=...`).

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

## Running the sync

```
python scripts/strava_to_notion.py --selfcheck   # offline sanity check of the pure helpers
python scripts/strava_to_notion.py               # real run
```

It looks at the newest `Source = Strava` row in Workouts and fetches activities after that date (or
the last `STRAVA_BACKFILL_DAYS` days if there are none yet), then creates one Workout row per new
activity. Running it twice in a row must report `0 created` the second time — that's the
duplicate-prevention working.

## GitHub Actions (the cron)

1. Push this repo to a **private** repo under your account
   (`git@github.com:ipungie/personal-task-tracker.git`).
2. Repo → Settings → Secrets and variables → Actions → add: `NOTION_TOKEN`, `NOTION_WORKOUTS_DB`,
   `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REFRESH_TOKEN`.
3. Actions tab → **strava-sync** → *Run workflow* to test. After that it runs every 6 hours.

Secrets are per-repo — if you ever move the repo to another host/account, re-add them there.

## Token rotation

If Strava sync starts failing with 401s, redo *Strava: one-time OAuth* above to get a fresh
`refresh_token`, then update both `.env` and the `STRAVA_REFRESH_TOKEN` GitHub secret.

## Adding Hevy later

Hevy's API needs a paid Hevy Pro subscription, so it's out for now. Until then, log lifts directly
in the Workouts database (`Source = Manual`). When you want automation, add
`scripts/hevy_csv_import.py` that parses Hevy's CSV export and upserts by
`External ID = hevy:<...>`, and give it its own workflow.
