#!/usr/bin/env python3
"""Import Hevy weight-training sessions into the Notion Workouts database.

Hevy's free "Export & Backup Data" gives a CSV (one row per set). Commit that export to the repo
as data/hevy.csv (or point HEVY_CSV_PATH elsewhere); this script groups it into one Workout row
per session. Idempotent: each session is keyed by `External ID = hevy:<start_time ISO>`, so
re-running against the same (or a re-exported, longer) CSV creates nothing already present.

The Hevy *API* would let this run unattended, but it needs Hevy Pro; the CSV path is $0.

Env (see .env.example): NOTION_TOKEN, NOTION_WORKOUTS_DB, optional HEVY_CSV_PATH.

Usage:
    python scripts/hevy_csv_to_notion.py            # run the import
    python scripts/hevy_csv_to_notion.py --selfcheck  # offline check of the pure helpers
"""
from __future__ import annotations

import csv
import io
import os
import sys
import time
from datetime import datetime

import requests

from notion_common import env, notion_headers

DEFAULT_CSV = "data/hevy.csv"

# ponytail: Hevy's export format varies by app version/locale. These cover what's been seen
# ("Aug 28, 2026 at 4:19 PM", "16 Jan 2024, 18:07", ISO); add a format here if an export doesn't parse.
_DT_FORMATS = (
    "%b %d, %Y at %I:%M %p", "%b %d, %Y at %I:%M:%S %p",
    "%d %b %Y, %H:%M", "%d %b %Y, %H:%M:%S",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
)


# --- pure helpers -----------------------------------------------------------------

def parse_dt(s: str | None) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _num(s: str | None) -> float:
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return 0.0


def _int(s: str | None) -> int | None:
    try:
        return int(float(str(s).strip()))
    except (TypeError, ValueError):
        return None


def fmtnum(v: float) -> str:
    return str(int(v)) if float(v) == int(v) else str(v)


def summarize_exercise(name: str, sets: list[tuple[int, float]]) -> str | None:
    """sets: [(reps, weight_kg)] for working sets only. Matches Quick Log's `Exercises` shape."""
    if not sets:
        return None
    reps = {r for r, _ in sets}
    weights = {w for _, w in sets}
    if len(reps) == 1 and len(weights) == 1:
        r, w = sets[0]
        return f"{name} {len(sets)}x{r} @{fmtnum(w)}kg" if w else f"{name} {len(sets)}x{r}"
    anyw = any(w for _, w in sets)
    parts = [f"{r}@{fmtnum(w)}" if w else f"{r}" for r, w in sets]
    return f"{name} " + ",".join(parts) + ("kg" if anyw else "")


def build_sessions(rows: list[dict]) -> dict[str, dict]:
    """Group set-rows into sessions keyed by the raw start_time string. Warm-up sets dropped."""
    sessions: dict[str, dict] = {}
    for row in rows:
        start_raw = (row.get("start_time") or "").strip()
        if not start_raw:
            continue
        s = sessions.setdefault(start_raw, {
            "title": (row.get("title") or "").strip() or "Hevy workout",
            "start_raw": start_raw,
            "end_raw": (row.get("end_time") or "").strip(),
            "ex": {},      # exercise name -> [(set_index, reps, weight)]
            "dist": 0.0,
        })
        if (row.get("set_type") or "").strip().lower() == "warmup":
            continue
        name = (row.get("exercise_title") or "").strip()
        if not name:
            continue
        s["ex"].setdefault(name, []).append(
            ((_int(row.get("set_index")) or 0), _int(row.get("reps")), _num(row.get("weight_kg")))
        )
        s["dist"] += _num(row.get("distance_km"))
    return sessions


def exercises_string(sess: dict) -> str:
    out = []
    for name, rows in sess["ex"].items():
        rows = sorted(rows, key=lambda t: t[0])
        # ponytail: timed holds (reps blank, e.g. planks) are skipped from the strength summary
        sets = [(r, w) for _, r, w in rows if r is not None]
        part = summarize_exercise(name, sets)
        if part:
            out.append(part)
    return "; ".join(out)


# --- Notion I/O -----------------------------------------------------------------

def latest_hevy_start(db_id: str) -> datetime | None:
    """Newest Date among existing Source=Hevy rows, or None. Lets us skip old CSV sessions."""
    r = requests.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=notion_headers(),
        json={
            "filter": {"property": "Source", "select": {"equals": "Hevy"}},
            "sorts": [{"property": "Date", "direction": "descending"}],
            "page_size": 1,
        },
        timeout=30,
    )
    r.raise_for_status()
    results = r.json()["results"]
    if not results:
        return None
    d = results[0]["properties"]["Date"]["date"]["start"]
    return datetime.fromisoformat(d.replace("Z", "+00:00")).replace(tzinfo=None)


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


def create_workout(db_id: str, sess: dict, start: datetime, ext: str) -> None:
    props: dict = {
        "Name": {"title": [{"text": {"content": sess["title"]}}]},
        "Date": {"date": {"start": start.isoformat()}},
        "Type": {"select": {"name": "Weights"}},
        "Source": {"select": {"name": "Hevy"}},
        "Exercises": {"rich_text": [{"text": {"content": exercises_string(sess)}}]},
        "External ID": {"rich_text": [{"text": {"content": ext}}]},
    }
    end = parse_dt(sess["end_raw"])
    if end and end > start:
        props["Duration"] = {"number": round((end - start).total_seconds() / 60, 1)}
    if sess["dist"]:
        props["Distance"] = {"number": round(sess["dist"], 2)}

    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers(),
        json={"parent": {"database_id": db_id}, "properties": props},
        timeout=30,
    )
    r.raise_for_status()


def run() -> None:
    path = os.environ.get("HEVY_CSV_PATH", DEFAULT_CSV)
    if not os.path.exists(path):
        print(f"no CSV at {path}; nothing to do")
        return
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    sessions = build_sessions(rows)
    print(f"{len(sessions)} sessions in {path}")

    db_id = env("NOTION_WORKOUTS_DB")
    latest = latest_hevy_start(db_id)
    created = skipped = 0
    for sess in sorted(sessions.values(), key=lambda s: s["start_raw"]):
        start = parse_dt(sess["start_raw"])
        if not start:
            print(f"skip: unparseable start_time {sess['start_raw']!r}")
            skipped += 1
            continue
        if latest and start <= latest:
            skipped += 1
            continue
        ext = f"hevy:{start.isoformat()}"
        if already_synced(db_id, ext):
            skipped += 1
            continue
        create_workout(db_id, sess, start, ext)
        created += 1
        time.sleep(0.35)  # ponytail: fixed sleep for Notion's ~3 req/s cap; swap for 429-retry if it bites
    print(f"done: {created} created, {skipped} skipped")


SAMPLE_CSV = """title,start_time,end_time,exercise_title,set_index,set_type,weight_kg,reps,distance_km,duration_seconds,rpe
legday funday,"16 Jan 2024, 18:07","16 Jan 2024, 18:56",Squat (Barbell),0,warmup,40,10,,,
legday funday,"16 Jan 2024, 18:07","16 Jan 2024, 18:56",Squat (Barbell),1,normal,100,5,,,
legday funday,"16 Jan 2024, 18:07","16 Jan 2024, 18:56",Squat (Barbell),2,normal,100,5,,,
legday funday,"16 Jan 2024, 18:07","16 Jan 2024, 18:56",Pull Up,1,normal,,8,,,
legday funday,"16 Jan 2024, 18:07","16 Jan 2024, 18:56",Pull Up,2,normal,,7,,,
push day,"18 Jan 2024, 07:30","18 Jan 2024, 08:10",Bench Press (Barbell),1,normal,60,8,,,
push day,"18 Jan 2024, 07:30","18 Jan 2024, 08:10",Bench Press (Barbell),2,normal,60,8,,,
push day,"18 Jan 2024, 07:30","18 Jan 2024, 08:10",Bench Press (Barbell),3,normal,62.5,7,,,
"""


def _selfcheck() -> None:
    assert parse_dt("Aug 28, 2026 at 4:19 PM") == datetime(2026, 8, 28, 16, 19)
    assert parse_dt("16 Jan 2024, 18:07") == datetime(2024, 1, 16, 18, 7)
    assert parse_dt("2024-01-16 18:07:00") == datetime(2024, 1, 16, 18, 7)
    assert parse_dt("") is None and parse_dt("garbage") is None
    assert fmtnum(60.0) == "60" and fmtnum(62.5) == "62.5"
    assert summarize_exercise("Bench", [(8, 60.0), (8, 60.0)]) == "Bench 2x8 @60kg"
    assert summarize_exercise("Pull Up", [(8, 0.0), (7, 0.0)]) == "Pull Up 8,7"
    assert summarize_exercise("Sq", [(5, 100.0), (7, 62.5)]) == "Sq 5@100,7@62.5kg"
    assert summarize_exercise("X", []) is None

    sessions = build_sessions(list(csv.DictReader(io.StringIO(SAMPLE_CSV))))
    assert len(sessions) == 2, sessions
    leg = sessions["16 Jan 2024, 18:07"]
    # warm-up row dropped -> 2 working squat sets, not 3
    assert [r for _, r, _ in leg["ex"]["Squat (Barbell)"]] == [5, 5]
    assert exercises_string(leg) == "Squat (Barbell) 2x5 @100kg; Pull Up 8,7", exercises_string(leg)
    push = sessions["18 Jan 2024, 07:30"]
    assert exercises_string(push) == "Bench Press (Barbell) 8@60,8@60,7@62.5kg", exercises_string(push)
    assert f"hevy:{parse_dt(leg['start_raw']).isoformat()}" == "hevy:2024-01-16T18:07:00"
    print("selfcheck ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        run()
