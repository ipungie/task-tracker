# AGENTS.md

Full repo guidance lives in **CLAUDE.md** — read it first. It is the source of truth for
architecture, Notion IDs, schema, and the build order.

## Working style for this repo

This repo runs the **ponytail** (laziest solution that works) and **caveman** (terse output)
skills. Apply them to every change here:

- **Ponytail** — climb the ladder before writing code: does it need to exist (YAGNI), is it
  already in the repo, does stdlib do it, does a native Notion feature cover it, is it one line.
  Regex over an LLM, a Notion embed over a sync, cron over a webhook. Mark deliberate corners
  with a `# ponytail:` comment naming the ceiling.
- **Caveman** — terse prose in chat. Normal English in code, comments, commits, docs, and
  anything written for other humans.

## Hard rules (see CLAUDE.md for the why)

- Notion is the single source of truth; scripts only write into it.
- One Notion database per domain; each integration is an independent script.
- Every synced Workout row carries an `External ID` — query by it before creating (upsert, never
  duplicate).
- Secrets never in the repo. `.env` is gitignored; real values are GitHub Actions secrets.
- `setup_notion.py` stays idempotent.
- Every non-trivial script keeps one `--selfcheck` / `assert` check for its pure logic. No test
  framework.
