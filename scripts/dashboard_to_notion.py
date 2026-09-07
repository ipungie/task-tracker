#!/usr/bin/env python3
"""Rebuild the MAIN HUB dashboard section in Notion: weekly training, body stats, tasks.

One independent script (like strava_to_notion.py). It owns a single `toggle` block on the MAIN HUB
page whose title starts with DASH_MARKER; every run deletes that toggle and re-appends a fresh one
pinned to the TOP of the page (via the Notion `position: {"type": "start"}` param, which needs
Notion-Version 2026-03-11 on that one request). Nothing else on the page is touched -- the Google
Calendar embed and linked-database views can't be created via the Notion API anyway, they stay a
one-time manual paste.

Env: NOTION_TOKEN (required). Optional overrides: NOTION_MAIN_HUB, NOTION_TASKS_DB,
NOTION_WORKOUTS_DB, NOTION_BODY_DB (defaults are the IDs recorded in CLAUDE.md / README.md).

Usage:
    python scripts/dashboard_to_notion.py
    python scripts/dashboard_to_notion.py --selfcheck   # offline check of the pure builders
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta

import requests

from notion_common import API, env, notion_headers, notion_query

# Notion added position:{type:"start"} (prepend as first child) in this API version.
# Used only for the one append-children call; the rest of the script stays on the default.
PREPEND_VERSION = "2026-03-11"

# IDs are public (they're in CLAUDE.md/README.md); only the token is a secret. Env overrides win.
MAIN_HUB = os.environ.get("NOTION_MAIN_HUB", "be220a6633b8426ab6f88e563d168e8a")
TASKS_DB = os.environ.get("NOTION_TASKS_DB", "3d4fdaa633b081638123f1307cd1f7af")
WORKOUTS_DB = os.environ.get("NOTION_WORKOUTS_DB", "3d4fdaa633b081cfbc6ed0d096baa224")
BODY_DB = os.environ.get("NOTION_BODY_DB", "3d4fdaa633b081bc8e1ac8e607ead38a")

DASH_MARKER = "📊 Weekly Dashboard"
LIFT_TARGET = 2
CARDIO_MIN_TARGET = 200
MAX_TASKS = 15
BODY_METRIC_ORDER = [
    "Weight", "Body Fat %", "SMM", "Body Fat Mass", "Visceral Fat",
    "BMI", "Waist-Hip Ratio", "InBody Score", "BMR",
]


# --- pure helpers -----------------------------------------------------------------

def _plain(rich: list[dict] | None) -> str:
    parts = []
    for t in rich or []:
        if "plain_text" in t:
            parts.append(t["plain_text"])
        elif t.get("type") == "text":
            parts.append(t.get("text", {}).get("content", ""))
    return "".join(parts)


def prop(row: dict, name: str) -> dict:
    return row.get("properties", {}).get(name, {})


def num(row: dict, name: str):
    return prop(row, name).get("number")


def date_val(row: dict, name: str):
    d = prop(row, name).get("date")
    return d.get("start") if d else None


def select_name(row: dict, name: str):
    p = prop(row, name)
    s = p.get("select") or p.get("status")  # tolerate a Status-type conversion in the UI
    return s.get("name") if s else None


def title_text(row: dict) -> str:
    for p in row.get("properties", {}).values():
        if p.get("type") == "title":
            return _plain(p.get("title", [])) or "(untitled)"
    return "(untitled)"


def to_go(done: float, target: float) -> float:
    return max(target - done, 0)


def fmtnum(v) -> str:
    if v is None:
        return "—"
    return str(int(v)) if float(v) == int(v) else f"{v:g}"


def fmt_delta(old: float, new: float) -> str:
    d = round(new - old, 2)
    return "0" if d == 0 else f"{d:+g}"


def _as_date(iso: str) -> date:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()


# --- block builders -------------------------------------------------------------

def _rt(content: str) -> list[dict]:
    return [{"type": "text", "text": {"content": content}}]


def h2(text: str) -> dict:
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rt(text)}}


def callout(text: str, emoji: str, color: str = "gray_background") -> dict:
    return {"object": "block", "type": "callout", "callout": {
        "rich_text": _rt(text), "icon": {"type": "emoji", "emoji": emoji}, "color": color}}


def todo(rich: list[dict]) -> dict:
    return {"object": "block", "type": "to_do", "to_do": {"rich_text": rich, "checked": False}}


def para(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rt(text) if text else []}}


# --- panels (pure: parsed rows in, block dicts out) ---------------------------------

def training_panel(workout_rows: list[dict], today: date) -> list[dict]:
    since = today - timedelta(days=7)
    lifts, cardio_min = 0, 0.0
    for row in workout_rows:
        ds = date_val(row, "Date")
        if not ds or _as_date(ds) < since:
            continue
        t = (select_name(row, "Type") or "").lower()
        if t == "weights":
            lifts += 1
        elif t == "cardio":
            cardio_min += num(row, "Duration") or 0
    cardio_min = round(cardio_min)
    lines = [
        f"Lifts    {lifts} / {LIFT_TARGET} sessions    — {to_go(lifts, LIFT_TARGET):g} to go",
        f"Cardio   {cardio_min} / {CARDIO_MIN_TARGET} min    — {to_go(cardio_min, CARDIO_MIN_TARGET):g} to go",
    ]
    return [h2("🏋️ This week"), callout("\n".join(lines), "🏋️", "blue_background")]


def body_panel(body_rows: list[dict]) -> list[dict]:
    rows = sorted((r for r in body_rows if date_val(r, "Date")), key=lambda r: date_val(r, "Date"))
    if not rows:
        return [h2("📊 Body"), callout("No body metrics logged yet.", "📊")]
    base, latest = rows[0], rows[-1]
    names = list(BODY_METRIC_ORDER)
    for r in (base, latest):
        for k, p in r.get("properties", {}).items():
            if p.get("type") == "number" and k not in names:
                names.append(k)
    lines = []
    for nm in names:
        b, l = num(base, nm), num(latest, nm)
        if b is None and l is None:
            continue
        if base is latest or b is None or l is None:
            lines.append(f"{nm}: {fmtnum(l if l is not None else b)}")
        else:
            lines.append(f"{nm}: {fmtnum(b)} → {fmtnum(l)}  ({fmt_delta(b, l)})")
    if base is latest:
        header = f"As of {date_val(latest, 'Date')[:10]}"
    else:
        header = f"InBody {date_val(base, 'Date')[:10]} → {date_val(latest, 'Date')[:10]}"
    body = header + ("\n" + "\n".join(lines) if lines else "\nno numeric fields")
    return [h2("📊 Body"), callout(body, "📊", "green_background")]


def tasks_panel(task_rows: list[dict], today: date) -> list[dict]:
    horizon = today + timedelta(days=7)
    picked = []
    for row in task_rows:
        status = select_name(row, "Status")
        if status == "Done":
            continue
        ds = date_val(row, "Due")
        due = _as_date(ds) if ds else None
        prio = select_name(row, "Priority")
        if not ((due is not None and due <= horizon) or prio == "High" or status == "Doing"):
            continue
        overdue = due is not None and due < today
        picked.append((overdue, due or date.max, row, due, prio))
    picked.sort(key=lambda t: (not t[0], t[1]))  # overdue first, then soonest due

    blocks = [h2(f"✅ Needs attention ({len(picked)})")]
    if not picked:
        blocks.append(para("Nothing overdue, due this week, high priority, or in progress. 🎉"))
        return blocks
    for overdue, _key, row, due, prio in picked[:MAX_TASKS]:
        tail = [due.isoformat() if due else "no date"] + ([prio] if prio else [])
        rich = []
        if overdue:
            rich.append({"type": "text", "text": {"content": "⚠️ "}})
        rich.append({"type": "mention", "mention": {"type": "page", "page": {"id": row["id"]}}})
        rich.append({"type": "text", "text": {"content": "  — " + " · ".join(tail)}})
        blocks.append(todo(rich))
    if len(picked) > MAX_TASKS:
        blocks.append(para(f"+ {len(picked) - MAX_TASKS} more"))
    return blocks


def build_section(workout_rows, body_rows, task_rows, today: date) -> list[dict]:
    return (training_panel(workout_rows, today)
            + body_panel(body_rows)
            + tasks_panel(task_rows, today))


# --- Notion I/O ---------------------------------------------------------------------

def _page_children(block_id: str) -> list[dict]:
    out, cursor = [], None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = requests.get(f"{API}/blocks/{block_id}/children", headers=notion_headers(), params=params, timeout=30)
        if r.status_code == 404:
            sys.exit(f"page {block_id} not found — share MAIN HUB with the integration")
        r.raise_for_status()
        data = r.json()
        out.extend(data["results"])
        if not data.get("has_more"):
            return out
        cursor = data["next_cursor"]


def find_marker_toggles(children: list[dict]) -> list[str]:
    """Ids of every top-level dashboard toggle (title starts with DASH_MARKER)."""
    return [b["id"] for b in children
            if b.get("type") == "toggle"
            and _plain(b.get("toggle", {}).get("rich_text", [])).startswith(DASH_MARKER)]


def rewrite_dashboard(section: list[dict]) -> None:
    for bid in find_marker_toggles(_page_children(MAIN_HUB)):
        r = requests.delete(f"{API}/blocks/{bid}", headers=notion_headers(), timeout=30)
        r.raise_for_status()
        time.sleep(0.35)  # ponytail: fixed sleep for Notion's ~3 req/s cap; swap for 429-retry if it bites
    toggle = {"object": "block", "type": "toggle", "toggle": {
        "rich_text": _rt(f"{DASH_MARKER}  ·  updated {date.today().isoformat()}"),
        "children": section,
    }}
    # position:{type:"start"} pins the toggle to the top of the page; needs the newer API version.
    r = requests.patch(f"{API}/blocks/{MAIN_HUB}/children",
                       headers=notion_headers(PREPEND_VERSION),
                       json={"children": [toggle], "position": {"type": "start"}}, timeout=30)
    r.raise_for_status()


def run() -> None:
    env("NOTION_TOKEN")  # fail fast with a clear message
    today = date.today()
    workouts = notion_query(WORKOUTS_DB, {
        "filter": {"property": "Date", "date": {"on_or_after": (today - timedelta(days=7)).isoformat()}},
        "page_size": 100,
    })
    body = notion_query(BODY_DB, {"sorts": [{"property": "Date", "direction": "ascending"}], "page_size": 100})
    tasks = notion_query(TASKS_DB, {"page_size": 100})

    section = build_section(workouts, body, tasks, today)
    rewrite_dashboard(section)
    n_tasks = sum(1 for b in section if b.get("type") == "to_do")
    print(f"dashboard updated: {len(workouts)} workouts in last 7d, {len(body)} body rows, "
          f"{n_tasks} tasks flagged")


# --- selfcheck --------------------------------------------------------------------

def _selfcheck() -> None:
    assert to_go(1, 2) == 1 and to_go(5, 2) == 0
    assert fmtnum(96.0) == "96" and fmtnum(31.4) == "31.4" and fmtnum(None) == "—"
    assert fmt_delta(96.3, 95.1) == "-1.2", fmt_delta(96.3, 95.1)
    assert fmt_delta(38.1, 38.4) == "+0.3", fmt_delta(38.1, 38.4)
    assert fmt_delta(12, 12) == "0"

    today = date(2026, 9, 7)

    def wk(t, when, dur=None):
        p = {"Type": {"type": "select", "select": {"name": t}},
             "Date": {"type": "date", "date": {"start": when}}}
        if dur is not None:
            p["Duration"] = {"type": "number", "number": dur}
        return {"properties": p}

    workouts = [wk("Weights", "2026-09-04"), wk("Weights", "2026-08-01"),
                wk("Cardio", "2026-09-06", 45), wk("Cardio", "2026-09-02", 50)]
    tp = training_panel(workouts, today)
    txt = _plain(tp[1]["callout"]["rich_text"])
    assert "Lifts    1 / 2 sessions    — 1 to go" in txt, txt
    assert "Cardio   95 / 200 min    — 105 to go" in txt, txt

    b0 = {"properties": {"Date": {"type": "date", "date": {"start": "2026-07-07"}},
                         "Weight": {"type": "number", "number": 96.3},
                         "Body Fat %": {"type": "number", "number": 31.0}}}
    b1 = {"properties": {"Date": {"type": "date", "date": {"start": "2026-09-01"}},
                         "Weight": {"type": "number", "number": 95.1},
                         "Body Fat %": {"type": "number", "number": 29.4}}}
    btxt = _plain(body_panel([b1, b0])[1]["callout"]["rich_text"])  # unsorted input on purpose
    assert "InBody 2026-07-07 → 2026-09-01" in btxt, btxt
    assert "Weight: 96.3 → 95.1  (-1.2)" in btxt, btxt
    assert "Body Fat %: 31 → 29.4  (-1.6)" in btxt, btxt
    assert _plain(body_panel([])[1]["callout"]["rich_text"]) == "No body metrics logged yet."
    assert "As of 2026-07-07" in _plain(body_panel([b0])[1]["callout"]["rich_text"])

    def task(title, status="Todo", due=None, prio=None):
        p = {"Name": {"type": "title", "title": [{"plain_text": title}]},
             "Status": {"type": "select", "select": {"name": status}},
             "Due": {"type": "date", "date": {"start": due} if due else None},
             "Priority": {"type": "select", "select": {"name": prio} if prio else None}}
        return {"id": "pg-" + title, "properties": p}

    tasks = [task("Overdue thing", due="2026-09-01"), task("Due soon", due="2026-09-10"),
             task("Far future low", due="2026-12-01"), task("Big rock", prio="High"),
             task("Working on it", status="Doing"), task("Finished", status="Done", due="2026-09-02")]
    tk = tasks_panel(tasks, today)
    assert _plain(tk[0]["heading_2"]["rich_text"]) == "✅ Needs attention (4)", _plain(tk[0]["heading_2"]["rich_text"])
    todos = [b for b in tk if b.get("type") == "to_do"]
    assert len(todos) == 4
    assert todos[0]["to_do"]["rich_text"][0]["text"]["content"] == "⚠️ "
    assert todos[0]["to_do"]["rich_text"][1]["mention"]["page"]["id"] == "pg-Overdue thing"

    kids = [{"id": "a", "type": "paragraph"}, {"id": "b", "type": "divider"},
            {"id": "c", "type": "toggle", "toggle": {"rich_text": [{"plain_text": DASH_MARKER + " · updated x"}]}},
            {"id": "d", "type": "paragraph"},
            {"id": "e", "type": "toggle", "toggle": {"rich_text": [{"plain_text": "unrelated"}]}}]
    assert find_marker_toggles(kids) == ["c"], find_marker_toggles(kids)
    print("selfcheck ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        run()
