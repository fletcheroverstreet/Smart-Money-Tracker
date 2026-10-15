# Smart Money Tracker: notes for Claude sessions

Read this first in every session.

- The full spec is saved verbatim in `docs/plan/SPEC.md`.
- The plan is `docs/plan/PLAN.md`. §14 lists the proposed deviations from the
  spec.
- The evidence behind every source decision is in
  `docs/plan/source-verification.md`.

## Status

- **Stage:** Phase 1 is done (signed off 2026-10-14). Waiting for the user's
  go for Phase 2. The plan's defaults are in force (see `docs/plan/PROGRESS.md`,
  "Decisions in force"). There is no git remote: the user uploads to GitHub
  themselves (`scripts/github_package.py` builds the drag-and-drop folders).
- **Resume from `docs/plan/PROGRESS.md`.** It has the checklist, the local
  toolchain paths and the autopilot rules.
- **Before doing anything, check `.autopilot/heartbeat`.** If it was written
  less than 90 minutes ago, another build session is active, so don't start
  work. While working, rewrite it (`date -Iseconds > .autopilot/heartbeat`)
  at least every 30 minutes.
- **`.autopilot/owner` names the session that holds the build.** An
  interactive session writes `interactive`; an autopilot run writes its own
  token and stands down whenever the file holds anything else, including
  right after a usage-limit pause. (On 2026-09-25 two sessions resumed at
  the same moment and edited the same files.)

## Where things are

- Repo: `C:\Users\fletc\Downloads\finlake_1\smart-money-tracker`, next to finlake
  and lodestar. It's a root folder in the user's VS Code workspace `main2`
  (`C:\Users\fletc\Downloads\finlake_1\finlake\finlake\main2.code-workspace`),
  listed after finlake and lodestar.
- finlake (read-only dependency, never modify): `C:\Users\fletc\Downloads\finlake_1\finlake`
- lodestar (reuse `industry_map.csv` only): `C:\Users\fletc\Downloads\finlake_1\lodestar`
- Obsidian vault project folder:
  `C:\Users\fletc\OneDrive\Documents\FinanceVault\01-Projects\smart-money-tracker\`
  Same structure as finlake: index note, `Decisions/DEC-###`, `Log/YYYY-MM-DD`,
  `Assets/`, `ISSUE-###`.

## House rules (from the spec and from finlake's history)

- Never fabricate data. Mock rows exist only in test fixtures.
- Raw first, parse second. Every fetched document is stored before parsing, and
  parsers are versioned so `reprocess` can rebuild from raw.
- Upserts are idempotent on natural keys. Ingesting twice changes nothing.
- A missing number is missing, never zero.
- Tests must never touch real data. `conftest.py` sets the environment before any
  import and refuses to run against a non-test database or against
  `~/.finlake`. (finlake's pytest once deleted 15.5M real facts because an
  import-time path was bound before the guard ran.)
- Every number traces to a filing. Every signal shows its inputs.
- Neutral language only: "late disclosure" and "timing overlap", never
  accusations.
- The user writes PowerShell on Windows, knows intermediate Python and no
  TypeScript. Code is typed, docstringed, and plain, with finance and legal
  logic explained in comments.
- Each phase ends with green tests, a demo checklist, a changelog, and a commit
  pushed to the remote (verify with `git log origin/main`). Then wait for the
  user's go.
- Ask before adding any paid service or deviating from the spec.

## Commands (PowerShell, from the repo root)

```powershell
.\scripts\db.ps1 start          # local Postgres 18 on port 5433 (no Docker needed)
.\scripts\dev.ps1               # db + migrations + API + worker + web, each in a window
.\scripts\test.ps1              # everything CI runs (ruff, mypy, pytest, eslint, tsc, next build)
.\scripts\backfill.ps1          # Forms 3/4/5 history from the SEC data sets
.\scripts\dev.ps1 -Docker       # the same stack in Docker Compose (needs Docker Desktop)
```

Backend by hand (inside `backend/`, after dot-sourcing `scripts\_env.ps1`):

- Migrations: `uv run python -m smt.cli migrate`
- Run: `uv run python -m smt.cli api` and `uv run python -m smt.cli worker`
- One job: `uv run python -m smt.cli run-job edgar.poll_ownership`
- Why `python -m smt.cli` and not `uv run smt`: on 2026-09-29 Windows Smart App
  Control started blocking the venv's `smt.exe` launcher ("An Application
  Control policy has blocked this file"). The module form runs the same CLI.
- If `uv run` itself fails with that error (it did for some hours on
  2026-10-12, when uv's own Python was blocked), use the signed python.org
  Python at `%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe`, with
  `PYTHONPATH` set to `backend\src` plus the venv's `Lib\site-packages`
  (`%USERPROFILE%\.smart-money-tracker\venv`).
- `run-job` is safe beside a running worker: it takes back only leases whose
  process has exited, and refuses a job the worker currently holds.
- Checks: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy`
- A new migration: `uv run alembic revision --autogenerate -m "..."`. Review
  the file before committing it.

Web (inside `web/`): `npm run dev`, `npm run build`, `npm run typecheck`,
`npm run lint`, `npm test` (Node's test runner on `src/**/*.test.ts`; test
files import with the `.ts` extension, so keep tested helpers free of `@/`
imports and React). To regenerate API types after changing a response model:
`npm run gen:openapi` (writes `src/lib/api/openapi.json` from the backend
code, no running API needed), then `npm run gen:api`.

## Conventions

- **Python.** Python 3.14. SQLAlchemy 2 typed models live in
  `backend/src/smt/db/models/`. Async psycopg 3.
  - Every async entry point starts through `smt.runtime.run()`, because
    Windows needs a SelectorEventLoop.
  - Settings come only from `smt.config.get_settings()`, never read at import
    time.
- **Structure.** Jobs are registered in `smt/worker/jobs.py`. API routers
  live in `smt/api/routers/`; a new router must be added to the list in
  `_include_optional_routers` in `smt/api/app.py`.
- **What counts in totals.** Every total, ranking and "today" figure uses the
  shared SQL in `smt/api/queries.py` (`COUNTED`, `PERSON_COUNTED`,
  `VALUE_OK`): current, open-market share trades, one copy per trade, no
  unpaired amendment rows, no impossible values (`smt/ingest/quality.py`).
  The People directory reads `entity_trade_stats` (migration 0008); change
  both together.
- **Web.** TypeScript 5.9, not 6 or 7 (openapi-typescript needs 5.x).
  TanStack Table is v8, which is stable and well documented; v9 is too new.
  Tailwind v4 tokens live in `web/src/app/globals.css`, mirroring the design
  sheet.
