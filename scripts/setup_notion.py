#!/usr/bin/env python3
"""One-shot: create the Tasks / Workouts / Body Metrics databases under a parent Notion page.

Idempotent — a database that already exists as a child of the page (matched by title) is not
recreated; instead any properties in the schema below that it's missing are added to it. So
adding a property here and re-running is enough to roll it out.
Views (calendar/board), the Google Calendar embed, and linked-database blocks are NOT creatable
via the Notion API; add those by hand afterwards.

    NOTION_TOKEN=... NOTION_PARENT_PAGE=<page id> python scripts/setup_notion.py

Notion API cannot create a `status`-type property, so Tasks.Status is a plain select here;
convert it to a Status property in the Notion UI if you want the nicer board grouping.
"""
from __future__ import annotations

import os
import sys

import requests

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def sel(*names: str) -> dict:
    return {"select": {"options": [{"name": n} for n in names]}}


SCHEMAS: dict[str, dict] = {
    "Tasks": {
        "Name": {"title": {}},
        "Status": sel("Todo", "Doing", "Blocked", "Done"),
        "Due": {"date": {}},
        "Priority": sel("High", "Medium", "Low"),
        "Project": {"select": {"options": []}},
        "Notes": {"rich_text": {}},
    },
    "Workouts": {
        "Name": {"title": {}},
        "Date": {"date": {}},
        "Type": sel("Cardio", "Weights", "Other"),
        "Source": sel("Strava", "Hevy", "Manual"),
        "Distance": {"number": {}},
        "Duration": {"number": {}},
        "Avg HR": {"number": {}},
        "Exercises": {"rich_text": {}},
        "External ID": {"rich_text": {}},
        "Link": {"url": {}},
    },
    "Body Metrics": {
        "Entry": {"title": {}},
        "Date": {"date": {}},
        "Weight": {"number": {}},
        "Body Fat %": {"number": {}},
        "Resting HR": {"number": {}},
        "BP": {"rich_text": {}},
        "Lab report": {"files": {}},
        "Notes": {"rich_text": {}},
        # InBody scan numbers — the dashboard's Body panel shows any of these that are present.
        "SMM": {"number": {}},
        "Body Fat Mass": {"number": {}},
        "Visceral Fat": {"number": {}},
        "BMI": {"number": {}},
        "Waist-Hip Ratio": {"number": {}},
        "InBody Score": {"number": {}},
        "BMR": {"number": {}},
    },
}


def headers() -> dict:
    tok = os.environ.get("NOTION_TOKEN")
    if not tok:
        sys.exit("missing NOTION_TOKEN")
    return {
        "Authorization": f"Bearer {tok}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def existing_child_dbs(page_id: str) -> dict[str, str]:
    """title -> database id, for child_database blocks directly under the page."""
    out: dict[str, str] = {}
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = requests.get(f"{API}/blocks/{page_id}/children", headers=headers(),
                         params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for b in data["results"]:
            if b["type"] == "child_database":
                out[b["child_database"]["title"]] = b["id"]
        if not data.get("has_more"):
            return out
        cursor = data["next_cursor"]


def patch_missing_props(db_id: str, want: dict) -> list[str]:
    """Add any properties in `want` the database doesn't already have. Returns the names added.

    ponytail: only adds; never renames or retypes an existing property, and won't touch a title
    prop that lives under a different name (would be a second title -> Notion 400).
    """
    r = requests.get(f"{API}/databases/{db_id}", headers=headers(), timeout=30)
    r.raise_for_status()
    have = set(r.json()["properties"])
    missing = {name: spec for name, spec in want.items() if name not in have}
    if not missing:
        return []
    r = requests.patch(f"{API}/databases/{db_id}", headers=headers(),
                       json={"properties": missing}, timeout=30)
    if r.status_code >= 400:
        sys.exit(f"patch {db_id} failed: {r.status_code} {r.text}")
    return sorted(missing)


def create_db(page_id: str, title: str, props: dict) -> str:
    r = requests.post(f"{API}/databases", headers=headers(), json={
        "parent": {"type": "page_id", "page_id": page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": props,
    }, timeout=30)
    if r.status_code >= 400:
        sys.exit(f"create {title!r} failed: {r.status_code} {r.text}")
    return r.json()["id"]


def main() -> None:
    page_id = os.environ.get("NOTION_PARENT_PAGE")
    if not page_id:
        sys.exit("missing NOTION_PARENT_PAGE")

    # fail early with a clear message if the page isn't shared with the integration
    r = requests.get(f"{API}/pages/{page_id}", headers=headers(), timeout=30)
    if r.status_code == 404:
        sys.exit("page not found or not shared with the integration — open the page, "
                 "••• > Connections > add your integration, then retry")
    r.raise_for_status()

    have = existing_child_dbs(page_id)
    for title, props in SCHEMAS.items():
        if title in have:
            added = patch_missing_props(have[title], props)
            note = f"  + {', '.join(added)}" if added else ""
            print(f"{title}: exists  {have[title].replace('-', '')}{note}")
            continue
        db_id = create_db(page_id, title, props).replace("-", "")
        print(f"{title}: created {db_id}")


if __name__ == "__main__":
    main()
