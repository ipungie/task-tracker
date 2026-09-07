#!/usr/bin/env python3
"""Pull recent Strava activities into the Notion Workouts database.

Idempotent: each activity is keyed by `External ID = strava:<id>`. Activities already
present in the database are skipped, so this is safe to run on a cron as often as you like.

Env (see .env.example): NOTION_TOKEN, NOTION_WORKOUTS_DB, STRAVA_CLIENT_ID,
STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN, optional STRAVA_BACKFILL_DAYS.

Usage:
    python scripts/strava_to_notion.py            # run the sync
    python scripts/strava_to_notion.py --selfcheck  # offline check of the pure helpers
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from notion_common import env, notion_headers

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"

# Strava sport_type -> Notion "Type" select. Anything unlisted falls through to "Other".
_WEIGHTS = {"WeightTraining", "Workout", "Crossfit"}
_CARDIO = {
    "Run", "TrailRun", "Ride", "GravelRide", "MountainBikeRide", "VirtualRide",
    "VirtualRun", "Swim", "Walk", "Hike", "Rowing", "Elliptical", "StairStepper",
}


def classify(sport_type: str) -> str:
    if sport_type in _WEIGHTS:
        return "Weights"
    if sport_type in _CARDIO:
        return "Cardio"
    return "Other"


def meters_to_km(m: float | None) -> float | None:
    return round(m / 1000, 2) if m else None


def seconds_to_min(s: float | None) -> float | None:
    return round(s / 60, 1) if s else None


def strava_access_token() -> str:
    r = requests.post(STRAVA_TOKEN_URL, data={
        "client_id": env("STRAVA_CLIENT_ID"),
        "client_secret": env("STRAVA_CLIENT_SECRET"),
        "grant_type": "refresh_token",
        "refresh_token": env("STRAVA_REFRESH_TOKEN"),
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def latest_strava_date(db_id: str) -> datetime:
    """Most recent Date among existing Source=Strava rows, or now - STRAVA_BACKFILL_DAYS."""
    r = requests.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=notion_headers(),
        json={
            "filter": {"property": "Source", "select": {"equals": "Strava"}},
            "sorts": [{"property": "Date", "direction": "descending"}],
            "page_size": 1,
        },
        timeout=30,
    )
    r.raise_for_status()
    results = r.json()["results"]
    if results:
        d = results[0]["properties"]["Date"]["date"]["start"]
        return datetime.fromisoformat(d.replace("Z", "+00:00"))
    days = int(env("STRAVA_BACKFILL_DAYS", "30"))
    return datetime.now(timezone.utc) - timedelta(days=days)


def fetch_activities(token: str, after: datetime) -> list[dict]:
    out: list[dict] = []
    page = 1
    after_epoch = int(after.timestamp())
    while True:
        r = requests.get(
            STRAVA_ACTIVITIES_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"after": after_epoch, "per_page": 200, "page": page},
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        out.extend(batch)
        page += 1
    return out


def already_synced(db_id: str, external_id: str) -> bool:
    r = requests.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=notion_headers(),
        json={"filter": {"property": "External ID", "rich_text": {"equals": external_id}},
              "page_size": 1},
        timeout=30,
    )
    r.raise_for_status()
    return bool(r.json()["results"])


def create_workout(db_id: str, act: dict) -> None:
    ext = f"strava:{act['id']}"
    props: dict = {
        "Name": {"title": [{"text": {"content": act.get("name") or "Activity"}}]},
        "Date": {"date": {"start": act["start_date_local"]}},
        "Type": {"select": {"name": classify(act.get("sport_type", ""))}},
        "Source": {"select": {"name": "Strava"}},
        "External ID": {"rich_text": [{"text": {"content": ext}}]},
        "Link": {"url": f"https://www.strava.com/activities/{act['id']}"},
    }
    km = meters_to_km(act.get("distance"))
    if km is not None:
        props["Distance"] = {"number": km}
    mins = seconds_to_min(act.get("moving_time"))
    if mins is not None:
        props["Duration"] = {"number": mins}
    if act.get("average_heartrate"):
        props["Avg HR"] = {"number": round(act["average_heartrate"])}

    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers(),
        json={"parent": {"database_id": db_id}, "properties": props},
        timeout=30,
    )
    r.raise_for_status()


def run() -> None:
    db_id = env("NOTION_WORKOUTS_DB")
    after = latest_strava_date(db_id)
    print(f"fetching Strava activities after {after.isoformat()}")
    activities = fetch_activities(strava_access_token(), after)
    print(f"{len(activities)} activities returned")

    created = skipped = 0
    for act in activities:
        ext = f"strava:{act['id']}"
        if already_synced(db_id, ext):
            skipped += 1
            continue
        create_workout(db_id, act)
        created += 1
        time.sleep(0.35)  # ponytail: fixed sleep for Notion's ~3 req/s cap; swap for 429-retry if it bites
    print(f"done: {created} created, {skipped} already present")


def _selfcheck() -> None:
    assert classify("Run") == "Cardio"
    assert classify("WeightTraining") == "Weights"
    assert classify("Kitesurf") == "Other"
    assert meters_to_km(5234) == 5.23
    assert meters_to_km(0) is None
    assert meters_to_km(None) is None
    assert seconds_to_min(1830) == 30.5
    assert seconds_to_min(None) is None
    print("selfcheck ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        run()
