# Architecture notes

Short map of the repository for people (and agents) changing it. User-facing text of the application is Russian;
code comments and technical docs for the work described in `CHANGELOG.md` are English.

## Stack

- Backend: Python 3.12, FastAPI, async SQLAlchemy 2 (asyncpg), Alembic, Postgres 17. One process (monolith).
- Frontend: React 19, Vite, TypeScript, Tailwind. No router library: hash routes parsed in `frontend/src/App.tsx`.
- Edge: Caddy serves the built frontend and proxies `/api/*` to the backend (`flush_interval -1` for SSE).
- Deployment: Docker Compose; GitHub Actions builds images, pushes them to ghcr.io and deploys over SSH.
- LLM: Yandex AI Studio (OpenAI-compatible API) and Yandex Search API v2. Provider and auth are out of scope for changes.

## Data model (`backend/app/db/models.py`)

| Table | Purpose |
|---|---|
| `categories`, `items`, `item_categories`, `catalog_syncs` | Frozen reference catalog (176 categories, 409 items), loaded from `backend/app/catalog/seed/catalog_snapshot.json`. |
| `runs` | One agent search: category (id nullable, name copied), location, neighbours flag, supplier type, status, stats (tokens, cost), timestamps. |
| `run_events` | Step feed of a run (also replayed over SSE). Event `usage` carries live spend. |
| `suppliers` | Unique suppliers by site domain; free-text `note`; `research` JSONB (pages studied, fields not found). |
| `supplier_fields` | One row per extracted value (`field`, `value`, `quote`, `source_url`, `verified_at`, `run_id`). Values are raw strings. |
| `run_suppliers` | Which suppliers a run found. |
| `fetched_pages` | Page cache (TTL 7 days). |

Structured prices, minimum orders, warnings and scores are **not stored**: they are derived from the raw
`supplier_fields` strings on every read (`GET /api/runs/{id}/suppliers`). Old history therefore re-scores
automatically and nothing needs backfilling.

## Where things live

- Agent loop and guards: `backend/app/agent/` (`loop.py`, `tools.py`, `validation.py`, `research.py`).
  - Agent system prompt and the first user message: `agent/prompts.py` (`SYSTEM_PROMPT`, `build_messages`).
  - Extraction schema and prompt (what the model returns per site): `agent/research.py` (`EXTRACTION_SYSTEM`, `parse_extraction`).
  - Every extracted field must pass quote verification in `agent/validation.py` before it is saved.
- Scoring: `backend/app/ranking/scoring.py` (weights are named constants at the top), location detection in `ranking/places.py`.
  `routes/runs.py::suppliers_of_run` builds the API payload and calls `rank_suppliers`.
- Run lifecycle (background tasks, daily limit, SSE): `backend/app/runs/service.py`, persistence in `runs/store.py`.
- Auth: `backend/app/auth.py` (signed cookie session, one user from env). All `/api` routers except health and auth are protected.
- Frontend:
  - `App.tsx` (auth gate, tabs, hash routing: `#search`, `#search/<id>`, `#history`, `#history/<id>`, `#catalog`)
  - `components/SearchPage.tsx` + `SearchForm.tsx` (form), `RunView.tsx` (progress block and result list),
    `SupplierCard.tsx`, `ComparisonTable.tsx`, `HistoryPage.tsx`, `CatalogPage.tsx`, `api.ts` (all HTTP calls, 401 handling).

## Conventions

- Backend tests are plain pytest. Tests that need Postgres run only when `TEST_DATABASE_URL` is set.
- Migrations are additive (`backend/alembic/versions/`, numbered); never rewrite old ones.
- A paid agent run costs about 70-100 RUB (cap about 250 RUB). Development uses the saved fixture
  `backend/tests/fixtures/search_rice_moscow.json` (stored result of the "Рис для суши", Москва search, read-only export).
- Frontend tests use Vitest and Testing Library (dev dependencies only) and run in CI.
