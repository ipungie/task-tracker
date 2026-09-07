#!/usr/bin/env python3
"""Shared Notion helpers.

Extracted once a 4th script needed the same `env()` / `notion_headers()` copy
(strava, hevy, dashboard, setup, notion_to_gcal). Import from a sibling script:

    from notion_common import env, notion_headers, notion_query, API

`scripts/` is not a package; running `python scripts/<x>.py` puts this dir on
sys.path, so the plain import resolves.
"""
from __future__ import annotations

import os
import sys

import requests

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        sys.exit(f"missing required env var: {name}")
    return v


def notion_headers(version: str | None = None) -> dict:
    return {
        "Authorization": f"Bearer {env('NOTION_TOKEN')}",
        "Notion-Version": version or NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_query(db_id: str, payload: dict) -> list[dict]:
    """POST /databases/{id}/query, following pagination. 404 -> clear exit message."""
    out: list[dict] = []
    cursor = None
    while True:
        body = dict(payload)
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(f"{API}/databases/{db_id}/query", headers=notion_headers(),
                          json=body, timeout=30)
        if r.status_code == 404:
            sys.exit(f"database {db_id} not found — share it with the integration "
                     "(••• > Connections)")
        r.raise_for_status()
        data = r.json()
        out.extend(data["results"])
        if not data.get("has_more"):
            return out
        cursor = data["next_cursor"]
