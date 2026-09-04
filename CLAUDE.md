# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AI PPT generator: topic / long text / uploaded document → editable outline → per-page concurrent generation → in-browser editor → quality-gated export of a native, editable PPTX. Monorepo with three runtimes plus a physically shared design-data directory:

- `backend/` — Python 3.12, FastAPI + ARQ worker, LangChain/LangGraph, python-pptx. The reference implementation.
- `java_backend/` — Spring Boot 3.5 / Java 21 port kept feature-aligned with the Python backend (same REST contract, same DB schema, same `shared/` data). langchain4j + langgraph4j, Apache POI for PPTX.
- `frontend/` — React 19 + TypeScript + Vite 8, Tailwind v4, TanStack Query, Zustand, react-router 8.
- `shared/` — JSON that both backends **and** the frontend load directly: `layouts/`, `themes/`, `flex-presets/`, plus parity fixtures (`flex-fixtures/`, `ambient-fixtures/`) and `sample-deck.json`.

Comments and user-facing strings are in Chinese; keep that convention.

## Commands

All dev ports are in the 39xxx range on purpose (Postgres 39432, Redis 39379, API 39800, Vite 39173). The Makefile at repo root is the canonical entry point.

```bash
make up                 # docker compose: postgres + redis
make install            # backend: uv sync; frontend: npm install
make fonts              # download Noto fonts to backend/fonts/ (not committed; needed for precise overflow metrics)
make migrate            # alembic upgrade head (run before starting either backend)
make dev-api            # uvicorn app.main:app --reload on 127.0.0.1:39800
make dev-worker         # arq app.worker.settings.WorkerSettings
make dev-web            # vite dev server on 127.0.0.1:39173, proxies /api -> :39800
make dev-java-api       # Spring Boot API (runs migrate first)
make dev-java-worker    # Spring Boot with profile=worker (its own Redis list queue, see below)
```

Config: copy `backend/.env.example` to `backend/.env`. `LLM_API_KEY` must be set for any generation to work (DeepSeek via OpenAI-compatible endpoint by default). The Java backend reads `java_backend/.env` and falls back to non-empty values in `backend/.env`, so one file usually suffices.

### Backend (run from `backend/`)

```bash
uv run pytest                                   # full suite; needs postgres+redis running (API tests use real DB via ASGITransport)
uv run pytest tests/test_flex_solve.py          # one file
uv run pytest tests/test_flex_solve.py -k name  # one test
uv run ruff check .        # lint (E,F,I,UP,B; line-length 100)
uv run ruff format .
make migration m="描述"    # alembic autogenerate + ruff format/fix on the new revision
make regression            # fixed corpus × 4 themes: 100% editability, overflow-slot rate < 5% (5% gate only enforced when fonts present)
uv run python scripts/compare_deck_metrics.py [deck.json]   # before/after metrics when touching layout/ambient code
```

Test conftest sets `STORAGE_LOCAL_DIR` to a tempdir before importing app modules because `Settings` and `Storage` are `lru_cache` singletons. Any new module that reads settings at import time must respect this ordering.

### Frontend (run from `frontend/`)

```bash
npm run dev
npm run build        # tsc -b && vite build
npm run lint         # oxlint (not eslint)
```

There is no test runner; correctness checks are standalone scripts asserting against `shared/` fixtures:

```bash
npx --yes tsx scripts/flex-parity.mts                                     # solver vs golden-*.json rects
npx --yes tsx --tsconfig tsconfig.app.json scripts/flex-normalize-parity.mts
npx --yes tsx --tsconfig tsconfig.app.json scripts/ambient-parity.mts
npx --yes tsx --tsconfig tsconfig.app.json scripts/flex-edit-check.mts
npx --yes tsx --tsconfig tsconfig.app.json scripts/bullets-edit-check.mts
```

### API types

`make gen-api` dumps the FastAPI OpenAPI schema (no server needed) to `frontend/openapi.json` and regenerates `frontend/src/api/schema.d.ts` via openapi-typescript. Run it after changing any Pydantic schema in `backend/app/schemas/` or `backend/app/domain/content.py`. Frontend types in `src/render/types.ts` are derived from that generated file, with a few local overrides (e.g. `FlexContainer`, `CardsBlock`, `CalloutBlock`) documented inline.

### Java backend (run from `java_backend/`)

```bash
mvn test
mvn -Dtest=FlexSolveTest test
mvn spring-boot:run -Dspring-boot.run.jvmArguments="-Dfile.encoding=UTF-8"
```

Java reuses the Alembic-managed schema; it has no migrations of its own. `DatabaseUrlProcessor` converts the `postgresql+asyncpg://` URL to JDBC.

## Architecture

### Request → job → SSE flow

1. API (`app/api/v1/*`) validates, writes to Postgres, enqueues an ARQ job (`app/core/queue.py`) and returns immediately.
2. Worker (`app/worker/`) runs `generate_outline` or `generate_deck`. `generate_deck` fans out per-slide with `asyncio.Semaphore(settings.slide_concurrency)` (default 3); whole-deck generation and single-slide retry share the same per-slide path (`_generate_one` → `workflows/slide.py`).
3. Progress goes through `services/events.EventStream`: every publish also writes a snapshot key to Redis so an SSE client reconnecting mid-job gets current state as its first frame. `api/sse.py` wraps a channel into `text/event-stream` with 15s heartbeat comments and terminates on terminal event types.
4. Frontend consumes SSE via `fetch` + manual parsing (`src/lib/sse.ts`) because `EventSource` can't send the `Authorization` header. Hooks: `useOutlineProgress`, `useDeckProgress`.

ARQ retry semantics matter: tasks must raise `arq.Retry` (via `worker/retry.py`) to be re-queued; any other exception is a permanent failure and the task must itself write a terminal failed state, otherwise the UI sits on "generating" forever. `LLMNotConfiguredError` is never retried. Cancellation is soft: a Redis flag checked before each slide starts; in-flight slides finish.

**Java worker is not interchangeable with the Python worker.** It consumes its own Redis list (`aippt:jobs`, `JobQueue.java`) rather than ARQ's queue, so run Java API with Java worker, or Python API with Python worker.

### Backend layering (`backend/app/`)

- `api/` — FastAPI routers. `api/v1/deck/` is split by concern (generation, pages, blocks, layout, ai_edit, export) with `_shared.py` holding the common guards: `_ensure_idle`, `_ensure_editable` (revision optimistic lock → 409), `_require_flex_tree`.
- `services/` — DB-touching orchestration. `services/deck.sync_slides` reconciles `slides` rows to outline pages using `outline_page_id` as idempotency key; anything that adds/removes pages must update the outline, the slide rows, and `project.page_count` together in one transaction (`services/deck_pages.py`) or the next sync will undo it.
- `workflows/` — LangGraph graphs. Pattern is *prepare → generate → check → (one repair round)*. Single structured LLM calls use LCEL `with_structured_output(method="json_mode")` (`llm/client.StructuredChatClient`); the graph only adds validation/repair state. `slide_edit` is the exception: it uses `bind_tools` and the model mutates an in-memory `EditSession` copy (`llm/edit_tools.py`), never the DB.
- `llm/` — `Protocol`-typed generators (`OutlineGenerator`, `SlideGenerator`, `SlideEditGenerator`) with DeepSeek implementations. Tests inject scripted generators; don't couple workflows to concrete classes.
- `domain/` — pure Pydantic models and functions, no I/O except reading `shared/`. This is where most logic lives and where the Java port mirrors file-for-file.
- `render/` — python-pptx export (`pptx.py`) and read-back verification (`verify.py`). Typography constants here must match `domain/text_metrics.py`.
- `ingest/` (txt/md/docx/pdf → sections with stable `S1:2`-style refs), `images/` (generated → Unsplash → placeholder fallback chain), `storage/` (local or Tencent COS behind a `Protocol`).

### The content / layout / theme split

- **Content** (`domain/content.py`): `Deck → Slide → Block[]`. Block types: text, bullets, image, chart, table, kpi, cards, callout. Blocks carry `locked` (protects human edits from AI rewrites) and optional per-block `style` overrides.
- **Layout** has two modes per slide. `fixed`: `layout_id` picks a `shared/layouts/*.json` with named slots and capacity hints; blocks bind by `slot_id`. `flex` (default for new slides): a `layout_tree` (`FlexContainer` of rows/columns/leaves) is solved into rects by `domain/flex_solve.py`; in flex mode `slot_id == block.id`. `domain/slide_geometry.resolve_slide_geometry` is the single entry that dispatches on mode.
- **Theme** (`shared/themes/*.json` + `domain/theme.py`): palette tokens, font pairs (web vs pptx latin/east-asian), text styles, and an `ambient` motif expanded by `domain/ambient.py` into rect/ellipse/text primitives that both Web and PPTX can draw natively. Project-level `theme_overrides` merge via `resolve_project_theme`.

Canvas is 960×540 pt (PPTX native unit); web scales by container width. Rects in JSON are normalized 0–1.

### Flex pipeline order (generation time)

`seed_layout_for_blocks` (preset match) → `flex_width.fit_row_widths` (min readable column width, may wrap cards) → `flex_fit.fit_tree_to_content` (grow from measured text height) → `flex_normalize.normalize` (ratio snapping, depth ≤ 4, ≤ 4 row children, grow clamps). Width must run before fit because height measurement depends on column width. `normalize` is idempotent; the frontend has a matching `flexNormalize.ts` and `flexLayout.ts` solver checked by the parity scripts.

### Dual-end parity is enforced by fixtures

Whenever you change solver, normalize, or ambient logic on one side, change the other and regenerate/verify fixtures:

- Solver: `shared/flex-presets/golden-*.json` carry `expected_rects`; checked by `tests/test_flex_solve.py` and `frontend/scripts/flex-parity.mts`.
- Normalize grows: `shared/flex-fixtures/normalize-grow-cases.json`.
- Ambient: regenerate with `backend/scripts/dump_ambient_fixture.py`, verify with `frontend/scripts/ambient-parity.mts`.

### Quality gates

`domain/validation.py` produces `StructureIssue{severity, code}`. `error` (slot contract broken, out-of-bounds) blocks export; `warning` (overflow, capacity, thin content, unsourced numbers, duplicate pages from `domain/quality.py`) does not. Repair rounds in the slide workflow only trigger on structural errors or thin/empty-phrase content, never on overflow warnings. `domain/export_check.run_export_check` is shared by the quality-report endpoint and the export endpoint; export then re-reads the PPTX with `render/verify.py`.

Text overflow uses FontTools glyph metrics against Noto fonts in `backend/fonts/`; when fonts are missing it falls back to per-char estimates and reports `fonts_precise=false`, and the regression 5% overflow threshold is skipped.

### Wallet and payments (modeled on sub2api)

Money is `Decimal` everywhere; never float. Users carry a `balance` column that is only ever changed by relative `UPDATE ... SET balance = balance ± x` in `services/payment.py`, and every change writes one `balance_ledger` row whose `code` is unique. That unique constraint is the idempotency guard: a recharge uses the order's `recharge_code`, a charge uses a caller-supplied code, so a duplicate webhook or retried job cannot credit twice.

Order state machine, all transitions are `UPDATE ... WHERE status IN (...)` plus `rowcount` checks:

```
PENDING → CANCELLED (user) | EXPIRED (cron sweeper or lazy on read)
PENDING / CANCELLED / EXPIRED-within-5min-grace → PAID → RECHARGING → COMPLETED
                                                                  └→ FAILED (retried on next notify/verify)
```

`handle_notification(session, notification, provider_key)` is the single funnel for webhooks, active `verify`, and the dev `mock-pay` endpoint. It rejects (audit row + `PaymentRejected`) on provider mismatch, merchant-snapshot mismatch, or amount mismatch beyond one cent. Cancel and the expiry sweeper first ask the gateway whether the order was actually paid, and fulfill instead of voiding if so.

Gateways live in `app/payment/providers/` behind a `Protocol` (`create_payment`, `query_order`, `verify_notification`, `merchant_snapshot`). `easypay` implements the EPay MD5 protocol (`api` mode posts `mapi.php` for a QR, `submit` mode builds a `submit.php` redirect; notify ack body must be the literal text `success`). `mock` is in-memory for local dev and tests and is never exposed at `/webhook/mock`. Selection is by env (`PAYMENT_PROVIDER`), there is no admin UI. `PAYMENT_PUBLIC_BASE_URL` must be the externally reachable origin because notify and return URLs are built from it.

Charging: `CHARGE_PER_PAGE` (default 0 = free) is pre-debited in the deck `generate` and `retry` endpoints via `charge_balance`; insufficient funds return 402. Retrying a failed page is not charged again. Tests set `PAYMENT_ENABLED=true` and `PAYMENT_PROVIDER=mock` in `tests/conftest.py` before settings are constructed.

### Frontend structure

- `src/api/client.ts` is the only fetch wrapper (auth header, 401 → token clear, `ApiError`). Binary downloads use `requestBinary`.
- `src/render/` is the shared slide renderer used by editor, filmstrip, and present mode; it loads layouts/themes from `shared/` at build time via `import.meta.glob` (`render/design.ts`), which is why `vite.config.ts` has `fs.allow: ['..']`.
- `src/features/deck/` is the editor. Saves go through `useSlideSaveQueue` (per-slide serial queue carrying `revision`; 409 → `conflict` status). Flex drag/resize logic is in `features/deck/flex/*` and `flexTree.ts`, operating on the same tree shape the backend solves.
- Routes: `/login`, `/projects`, `/create` inside `AppShell`; `/projects/:projectId` is the full-screen outline/editor workbench.

## Deployment

`docker-compose.prod.yml` builds `backend/Dockerfile` for both `api` and `worker`, mounts `./shared` read-only at `/shared`, and serves `frontend/dist` through nginx on 39880 with `/api/` proxied (buffering off for SSE). Build the frontend first; the compose file does not build it. The Dockerfile runs `scripts/fetch_fonts.py` best-effort at image build.
