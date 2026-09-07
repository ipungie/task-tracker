#!/usr/bin/env python3
"""One-way sync: Notion -> Google Calendar.

Pushes two things onto a Google Calendar so they show up (with reminders) on the
user's phone:

- **Tasks** with a `Due` date and `Status != Done`  -> an all-day event on the due
  date, marked free/transparent, with a popup reminder the morning of.
- **Events** (a small Notion DB used via a Calendar view) with a `When` value ->
  a timed event (or all-day if `When` has no time).

Idempotent: the Google event id is derived from the Notion page id
(`nt<hex32>`), so a re-run updates in place instead of duplicating -- nothing is
written back to Notion. When a Notion row goes away (deleted, marked Done, date
cleared), its Google event is removed on the next run. Only events tagged
`extendedProperties.private.notionSync = "1"` are ever touched, so hand-made
calendar events are safe.

Env: NOTION_TOKEN, NOTION_EVENTS_DB, GCAL_SA_JSON (service-account key JSON),
GCAL_CALENDAR_ID (the calendar's id, e.g. your gmail address -- never "primary").
Optional: NOTION_TASKS_DB (defaults to the baked id), GCAL_TZ (default Asia/Jakarta).

Usage:
    python scripts/notion_to_gcal.py
    python scripts/notion_to_gcal.py --selfcheck   # offline check of the pure helpers
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time

from notion_common import env, notion_query

TASKS_DB = os.environ.get("NOTION_TASKS_DB", "3d4fdaa633b081638123f1307cd1f7af")
EVENTS_DB = os.environ.get("NOTION_EVENTS_DB")  # no baked default -- created by setup_notion.py
GCAL_TZ = os.environ.get("GCAL_TZ", "Asia/Jakarta")

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TASK_REMINDER_MIN = 540  # ponytail: fixed 09:00 local (minutes from midnight); make an env if asked
LOOKBACK_DAYS = 7        # ponytail: delete-reconcile window; a row deleted >7d after its date won't be cleaned
LOOKAHEAD_DAYS = 400


# --- pure helpers (covered by --selfcheck) ---------------------------------------

def event_id(page_id: str) -> str:
    """Deterministic Google Calendar event id from a Notion page id.

    Notion page id is a hex UUID; stripped of hyphens it's 32 chars of [0-9a-f],
    which satisfies Google's id rule (lowercase a-v + digits, length 5-1024).
    """
    hex32 = page_id.replace("-", "").lower()
    return "nt" + hex32


def _plain(rich: list[dict] | None) -> str:
    out = []
    for t in rich or []:
        out.append(t.get("plain_text") or t.get("text", {}).get("content", "") or "")
    return "".join(out)


def _title(props: dict) -> str:
    for p in props.values():
        if p.get("type") == "title":
            return _plain(p.get("title", [])) or "(untitled)"
    return "(untitled)"


def _select_name(prop: dict):
    s = prop.get("select") or prop.get("status")  # tolerate a Status-type conversion in the UI
    return s.get("name") if s else None


def _has_time(iso: str) -> bool:
    return "T" in iso


def _has_offset(iso: str) -> bool:
    return bool(re.search(r"T\d\d:\d\d.*(Z|[+-]\d\d:?\d\d)$", iso))


def _parse_iso(s: str) -> dt.datetime:
    s = s.replace("Z", "+00:00")
    s = re.sub(r"\.\d+", "", s)  # drop fractional seconds (py3.9 fromisoformat is picky)
    return dt.datetime.fromisoformat(s)


def _plus_hour(iso: str) -> str:
    return (_parse_iso(iso) + dt.timedelta(hours=1)).isoformat()


def _next_day(date_str: str) -> str:
    return (dt.date.fromisoformat(date_str[:10]) + dt.timedelta(days=1)).isoformat()


def _tagged(page_id: str, kind: str) -> dict:
    return {"private": {"notionSync": "1", "notionPageId": page_id, "kind": kind}}


def task_event_body(row: dict) -> dict | None:
    """All-day 'task due' event, or None if the row has no Due date."""
    props = row.get("properties", {})
    d = (props.get("Due", {}) or {}).get("date")
    if not d or not d.get("start"):
        return None
    day = d["start"][:10]
    return {
        "summary": "📋 " + _title(props),
        "start": {"date": day},
        "end": {"date": _next_day(day)},
        "transparency": "transparent",  # free, not busy
        # ponytail: no "visibility":"private" — an unauthenticated Notion /embed of a public
        # calendar strips private events, and it's your own calendar anyway.
        "reminders": {"useDefault": False,
                      "overrides": [{"method": "popup", "minutes": TASK_REMINDER_MIN}]},
        "extendedProperties": _tagged(row["id"], "task"),
    }


def event_event_body(row: dict) -> dict | None:
    """Timed meeting event (all-day if `When` has no time), or None if `When` is empty."""
    props = row.get("properties", {})
    w = (props.get("When", {}) or {}).get("date")
    if not w or not w.get("start"):
        return None
    start, end = w["start"], w.get("end")
    body: dict = {
        "summary": _title(props),
        "reminders": {"useDefault": True},
        "extendedProperties": _tagged(row["id"], "event"),
    }
    loc = _plain((props.get("Location", {}) or {}).get("rich_text"))
    if loc:
        body["location"] = loc

    if not _has_time(start):                       # date-only -> all-day
        body["start"] = {"date": start[:10]}
        body["end"] = {"date": _next_day(end or start)}
        return body

    def _dt(v: str) -> dict:
        return {"dateTime": v} if _has_offset(v) else {"dateTime": v, "timeZone": GCAL_TZ}

    body["start"] = _dt(start)
    body["end"] = _dt(end or _plus_hour(start))
    return body


def stale_event_ids(owned_ids, live_ids) -> list[str]:
    """Google event ids we own (notionSync=1) that no longer map to a live Notion row."""
    live = set(live_ids)
    return [i for i in owned_ids if i not in live]


# --- Notion I/O ----------------------------------------------------------------------

def fetch_tasks() -> list[dict]:
    rows = notion_query(TASKS_DB, {"page_size": 100})
    out = []
    for r in rows:
        props = r.get("properties", {})
        if _select_name(props.get("Status", {})) == "Done":
            continue
        d = (props.get("Due", {}) or {}).get("date")
        if d and d.get("start"):
            out.append(r)
    return out


def fetch_events() -> list[dict]:
    rows = notion_query(EVENTS_DB, {"page_size": 100})
    return [r for r in rows if ((r.get("properties", {}).get("When", {}) or {}).get("date") or {}).get("start")]


# --- Google Calendar I/O -----------------------------------------------------------
# ponytail: google libs imported lazily so --selfcheck runs without the dependency.

def gcal_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = json.loads(env("GCAL_SA_JSON"))
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _retry(fn, tries: int = 4):
    from googleapiclient.errors import HttpError
    for i in range(tries):
        try:
            return fn()
        except HttpError as e:
            if getattr(e, "resp", None) is not None and e.resp.status in (403, 429, 500, 502, 503) and i < tries - 1:
                time.sleep(2 ** i)
                continue
            raise


def upsert(svc, cal_id: str, eid: str, body: dict) -> str:
    from googleapiclient.errors import HttpError
    try:
        _retry(lambda: svc.events().insert(calendarId=cal_id, body={**body, "id": eid}).execute())
        return "created"
    except HttpError as e:
        if e.resp is not None and e.resp.status == 409:  # id exists -> update in place
            _retry(lambda: svc.events().patch(calendarId=cal_id, eventId=eid, body=body).execute())
            return "updated"
        raise


def delete_event(svc, cal_id: str, eid: str) -> None:
    from googleapiclient.errors import HttpError
    try:
        _retry(lambda: svc.events().delete(calendarId=cal_id, eventId=eid).execute())
    except HttpError as e:
        if e.resp is None or e.resp.status not in (404, 410):  # already gone -> fine
            raise


def list_owned(svc, cal_id: str) -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    time_min = (now - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    time_max = (now + dt.timedelta(days=LOOKAHEAD_DAYS)).isoformat()
    out, token = [], None
    while True:
        resp = _retry(lambda: svc.events().list(
            calendarId=cal_id, privateExtendedProperty="notionSync=1",
            timeMin=time_min, timeMax=time_max, showDeleted=False,
            singleEvents=True, maxResults=2500, pageToken=token,
        ).execute())
        out.extend(resp.get("items", []))
        token = resp.get("nextPageToken")
        if not token:
            return out


# --- run -------------------------------------------------------------------------

def run() -> None:
    missing = [k for k in ("GCAL_SA_JSON", "GCAL_CALENDAR_ID", "NOTION_EVENTS_DB")
               if not os.environ.get(k)]
    if missing:
        print(f"skip: not configured yet ({', '.join(missing)})")
        return
    cal_id = os.environ["GCAL_CALENDAR_ID"]
    svc = gcal_service()

    tasks, events = fetch_tasks(), fetch_events()
    live: dict[str, dict] = {}
    for r in tasks:
        b = task_event_body(r)
        if b:
            live[event_id(r["id"])] = b
    for r in events:
        b = event_event_body(r)
        if b:
            live[event_id(r["id"])] = b

    created = updated = 0
    for eid, body in live.items():
        res = upsert(svc, cal_id, eid, body)
        created += res == "created"
        updated += res == "updated"
        time.sleep(0.1)

    owned = list_owned(svc, cal_id)
    stale = stale_event_ids([e["id"] for e in owned], live.keys())
    for eid in stale:
        delete_event(svc, cal_id, eid)
        time.sleep(0.1)

    print(f"gcal sync: {created} created, {updated} updated, {len(stale)} deleted "
          f"({len(tasks)} tasks, {len(events)} events)")


# --- selfcheck -----------------------------------------------------------------------

def _selfcheck() -> None:
    assert event_id("3d4fdaa6-33b0-8163-8123-f1307cd1f7af") == "nt3d4fdaa633b081638123f1307cd1f7af"
    assert len(event_id("3d4fdaa6-33b0-8163-8123-f1307cd1f7af")) == 34

    task = {"id": "11111111-1111-1111-1111-111111111111", "properties": {
        "Name": {"type": "title", "title": [{"plain_text": "Ship report"}]},
        "Due": {"type": "date", "date": {"start": "2026-09-10"}},
        "Status": {"type": "select", "select": {"name": "Todo"}}}}
    b = task_event_body(task)
    assert b["summary"] == "📋 Ship report"
    assert b["start"] == {"date": "2026-09-10"} and b["end"] == {"date": "2026-09-11"}, b
    assert b["transparency"] == "transparent"
    assert b["reminders"]["overrides"][0]["minutes"] == 540
    assert b["extendedProperties"]["private"] == {
        "notionSync": "1", "notionPageId": task["id"], "kind": "task"}
    task["properties"]["Due"]["date"]["start"] = "2026-09-10T15:00:00.000+07:00"
    assert task_event_body(task)["start"] == {"date": "2026-09-10"}  # datetime Due -> all-day on the date
    task["properties"]["Due"]["date"] = None
    assert task_event_body(task) is None

    ev = {"id": "22222222-2222-2222-2222-222222222222", "properties": {
        "Name": {"type": "title", "title": [{"plain_text": "1:1 with Sam"}]},
        "When": {"type": "date", "date": {"start": "2026-09-10T14:00:00+07:00", "end": None}},
        "Location": {"type": "rich_text", "rich_text": [{"plain_text": "Cafe"}]}}}
    eb = event_event_body(ev)
    assert eb["start"] == {"dateTime": "2026-09-10T14:00:00+07:00"}, eb   # offset present -> no timeZone
    assert eb["end"] == {"dateTime": "2026-09-10T15:00:00+07:00"}, eb     # start + 1h
    assert eb["location"] == "Cafe" and eb["reminders"] == {"useDefault": True}
    ev["properties"]["When"]["date"]["end"] = "2026-09-10T15:30:00+07:00"
    assert event_event_body(ev)["end"] == {"dateTime": "2026-09-10T15:30:00+07:00"}
    ev["properties"]["When"]["date"] = {"start": "2026-09-12T09:00:00", "end": None}
    assert event_event_body(ev)["start"] == {"dateTime": "2026-09-12T09:00:00", "timeZone": GCAL_TZ}
    ev["properties"]["When"]["date"] = {"start": "2026-09-12", "end": None}
    assert event_event_body(ev)["start"] == {"date": "2026-09-12"}
    assert event_event_body(ev)["end"] == {"date": "2026-09-13"}
    ev["properties"]["When"]["date"] = {"start": "2026-09-12", "end": "2026-09-14"}
    assert event_event_body(ev)["end"] == {"date": "2026-09-15"}  # all-day end is exclusive
    ev["properties"]["When"]["date"] = None
    assert event_event_body(ev) is None

    assert stale_event_ids(["a", "b", "c"], {"b"}) == ["a", "c"]
    assert stale_event_ids(["nt1", "nt2"], ["nt1", "nt2"]) == []
    print("selfcheck ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        run()
