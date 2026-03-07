# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A **general-purpose text-to-SQL data agent framework**: users ask natural language questions about any data store connected through MindsDB, GPT generates and executes SQL, returns answers via a Chainlit chat UI. Currently tested against US Census ACS 5-Year data (2019-2023) as the reference dataset.

## Design Philosophy — Data-Store Agnostic

**The framework MUST accommodate any data store plugged into MindsDB — not just Census data.** Census is the current test harness, but the agent core (tool loop, error handling, streaming, CSV export, DML protection, temperature ramp) is generic.

Census-specific parts exist and must be kept fenced so they can be extracted as configuration:

| Census-Specific Part | Location | How to Generalize |
|---------------------|----------|-------------------|
| `SYSTEM_PROMPT` (ACS table names, geographies schema, year ranges, Census-specific SQL examples) | `src/agent_client.py:30-63` | Move to a config file or env var; each data store provides its own prompt describing schema, conventions, and examples |
| `_SEARCH_STOP_WORDS` | `src/agent_client.py:20-28` | English stop words are generic; Census-specific words (if any) should be in config |
| `SEARCH_CATALOG_TOOL` description (ACS table titles, variable labels) | `src/agent_client.py:66-86` | Tool description should be parameterized per data store |
| `_search_catalog_tables()` / `_search_catalog()` (searches `acs_catalog` table) | `src/agent_client.py:177-277` | Catalog table name and column names should come from config; search logic is generic |
| `_auto_search_catalog()` (looks up `acs_catalog` by `table_id`) | `src/agent_client.py:286-303` | Same — table name from config |
| ETL scripts (`load_all_acs.py`, `init_db.sql`, `seed_data.sql`) | `scripts/` | Entirely Census-specific; other data stores bring their own ETL |
| `setup_mindsdb.py` (creates `census_db` connection name) | `scripts/setup_mindsdb.py` | Connection name should be configurable |

**When modifying code, always ask: "Does this change work for a non-Census data store?"** If a change hardcodes Census-specific assumptions (table naming patterns like `acs_*`, column names like `geo_id`/`data_year`, geography concepts like states/counties), it must be fenced behind configuration, not baked into the agent core.

## Commands

```bash
# Full stack (PostgreSQL + MindsDB + data-agent)
docker compose up --build -d

# Rebuild only the app after code changes
docker compose up --build -d data-agent

# Logs
docker compose logs -f data-agent
docker compose logs mindsdb --tail 100

# Teardown (preserves data volume)
docker compose down
# Teardown + wipe data
docker compose down -v

# Direct SQL against MindsDB (from inside container)
docker compose exec data-agent python3 -c "
import mindsdb_sdk
s = mindsdb_sdk.connect('http://mindsdb:47334')
print(s.query('SELECT * FROM census_db.acs_b01001 LIMIT 5').fetch())
"

# Direct SQL against PostgreSQL
docker compose exec postgres psql -U census_admin -d census_data -c "SELECT count(*) FROM geographies"

# Run e2e tests
docker compose exec data-agent pytest tests/test_e2e.py -v --timeout=180
```

End-to-end tests exist — see Testing section below.

## Architecture

Three Docker containers:

```
Browser :8001 → data-agent (Chainlit + OpenAI agent) → MindsDB :47334 → PostgreSQL :5432
```

**data-agent** is the only container with custom code. MindsDB and PostgreSQL are stock images.

### Agent Flow (src/agent_client.py)

The agent calls OpenAI directly (not MindsDB's built-in agent, which truncates results to 30 rows). Two tools:

- **sql_query**: for analytical questions (top-N, aggregations, comparisons). Returns CSV text in LLM context for GPT to interpret and present.
- **export_csv**: for data exports (CSV downloads, "all rows", large result sets). Writes full CSV to a temp file, Chainlit delivers it as a download. GPT writes a short summary instead of pasting data.

The agent loop:
1. Build messages: system prompt + conversation history + user question
2. Call `client.chat.completions.create()` with both tool definitions
3. If GPT returns a tool call → execute SQL via `mindsdb_sdk.connect().query()` → append result → loop
4. If GPT returns `finish_reason == "stop"` → return answer + any file exports
5. Max 10 iterations as safety cap

**Why not MindsDB's built-in agent?** MindsDB's agent uses LangChain internally with a hardcoded `limit_rows = 30` in `sql_agent.py:625`. This truncates SQL results before GPT sees them, making it impossible to return full datasets. We bypass the agent layer but keep MindsDB as the SQL proxy — queries still flow through `mindsdb_sdk.query()` and hit the `census_data` view that enforces column suppression.

**Why not bypass MindsDB entirely (e.g., psycopg2/SQLAlchemy)?** The production database is read-only and unmodifiable. MindsDB is the layer that creates views, suppresses columns, and reshapes the schema without touching the source database. This is the core architectural constraint the POC must prove. Do not bypass MindsDB with direct database connections.

### Chat UI (src/app.py)

Chainlit handles WebSocket, session state, and rendering. Two modes controlled by `STREAM_THINKING` env var:
- **Streaming** (default): yields `{"steps": [...]}` dicts rendered as expandable accordions showing SQL + results, then `{"output": "..."}` for the final answer
- **Blocking**: returns a single string

Conversation history stored in `cl.user_session` (per-tab, in-memory, lost on refresh). Capped at 50 turns.

### Data Layer (Census-Specific)

> **This entire section is Census-specific.** A different data store would have its own schema, ETL, and catalog structure. The agent framework doesn't depend on this layout.

PostgreSQL holds ~1,193 ACS tables (`acs_b01001`, `acs_b02001`, etc.) loaded by `scripts/load_all_acs.py`, plus:

| Table | Content |
|-------|---------|
| `geographies` | FIPS codes, names, area_type (state/county), state_fips, county_fips |
| `acs_catalog` | Variable-level metadata: table_id, variable_id, label, table_title, is_estimate |
| `acs_*` (×1,193) | One table per ACS subject (e.g., `acs_b19013` = median household income). Each has `geo_id`, `data_year`, and variable columns |

52 states (50 + DC + PR), 3,222 counties. No census tracts. Years 2019-2023.

### Bootstrap (scripts/entrypoint.sh)

Idempotent. On container start:
1. Check if `geographies` has data → skip ETL if yes
2. If `CENSUS_API_KEY` set → fetch from Census API (`load_all_acs.py`); else → error
3. Run `setup_mindsdb.py` (creates PG connection `census_db`)
4. Start Chainlit

## Key Environment Variables

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| OPENAI_API_KEY | Yes | — | Used by agent_client.py directly |
| CENSUS_API_KEY | No | — | If missing, seed data used instead |
| LLM_MODEL | No | gpt-4.1 | Any OpenAI model name |
| STREAM_THINKING | No | "1" | "1"/"true"/"yes" enables streaming steps UI |
| MINDSDB_HOST | No | http://localhost:47334 | Set to http://mindsdb:47334 in Docker |

## MindsDB SQL Pitfalls

These are hard-won lessons — do not repeat these mistakes:

- **No table aliases**: `FROM housing AS h` breaks because MindsDB injects `WHERE housing.col` but PG expects alias `h`
- **No IN(...) with AND**: `WHERE state_name IN ('TX','CA') AND data_year = 2023` fails. Use `(state_name = 'TX' OR state_name = 'CA') AND data_year = 2023`
- **No multi-table JOINs in views**: MindsDB injects bare `WHERE data_year = 2022` which is ambiguous across tables. Use scalar subqueries instead
- **FQN doesn't help**: Even `census_db.demographics.data_year` in SELECT still gets bare `WHERE data_year = ...` injected by MindsDB
- **Cannot modify production PostgreSQL**: Views/roles/tables in PG are for defense-in-depth only; MindsDB views are the primary enforcement layer

## Testing

End-to-end tests call `query_agent()` directly inside the Docker container against live MindsDB + PostgreSQL. No mocks — these are full-stack integration tests.

```bash
# Run all tests (requires running Docker stack)
docker compose exec data-agent pytest tests/test_e2e.py -v --timeout=180

# Run a single test
docker compose exec data-agent pytest tests/test_e2e.py::test_q6_median_income_states -v
```

8 test cases covering: median income queries, race-by-state, multi-turn CSV export, Pearson correlation, census tract edge cases. Tests validate response content with keyword/pattern matching — they accept multiple valid agent behaviors (e.g., inline table vs CSV export for large results).

Tests are Census-specific by nature (they ask Census questions), but the test *infrastructure* (`conftest.py`, assertion helpers) is generic.

## Design Documents

- `docs/plans/2026-02-05-mindsdb-data-agent-design.md` — Full system design (14 steps, 5 phases)
- `docs/schema-tldr.md` — ER diagram, column descriptions, suppression list
- `docs/analysis/2026-02-05-chainlit-vs-streamlit-comparison.md` — Framework decision rationale
