# CLAUDE.md

This file gives code-focused guidance for working in this repository.

## What This Repo Is

This repository currently implements a Census-specific text-to-SQL chat app.

Runtime stack:

`browser -> Chainlit -> OpenAI tool-calling agent -> MindsDB -> PostgreSQL`

The active implementation is documented in:

- `docs/analysis/2026-03-07-current-implementation-design.md`
- `docs/analysis/2026-02-05-chainlit-vs-streamlit-comparison.md` (historical framework-selection rationale)

## Current Runtime Truth

- `src/app.py` is the Chainlit entrypoint
- `src/agent_client.py` contains the OpenAI tool loop
- `scripts/entrypoint.sh` is the active bootstrap path
- `scripts/setup_mindsdb.py` creates the `census_db` MindsDB connection
- `scripts/load_from_files.py` is the primary ETL path for the product runtime
- `scripts/load_all_acs.py` is a testing/fallback loader, not the main product data path
- `scripts/load_census_data.py` and `scripts/seed_data.sql` are legacy artifacts, not active runtime paths

## Current Architecture

Three Docker services:

```text
Browser :8001 -> data-agent :8000 -> MindsDB :47334 -> PostgreSQL :5432
```

Only `data-agent` contains custom application code.

### Agent Flow

The agent calls OpenAI directly. MindsDB is used as a SQL proxy/namespace, not as the agent runtime.

Current tool surface in `src/agent_client.py`:

1. `search_catalog`
   - searches `census_db.acs_catalog`
   - returns CSV rows with `table_id`, `table_title`, `variable_id`, and `label`
2. `sql_query`
   - executes read-only SQL and returns CSV text
   - truncates after `MAX_QUERY_ROWS = 500`
3. `export_csv`
   - executes read-only SQL and writes a temp CSV for download
   - caps output at `MAX_EXPORT_ROWS = 100_000`

Key loop behavior:

- builds messages from system prompt + last 50 turns + current question
- allows up to 10 OpenAI iterations
- increases temperature when `sql_query` or `export_csv` returns an error or empty result
- aborts after 3 consecutive failed SQL/export iterations

### Chat UI

`src/app.py`:

- stores session history in `cl.user_session`
- sends only the last 50 turns back to the model
- runs in streaming mode for the product runtime because `docker-compose.yml` sets `STREAM_THINKING="1"`

### Data Layer

Current PostgreSQL schema centers on:

- `geographies`
- `acs_catalog`
- one `acs_*` table per ACS table group

Important ETL truth:

- `load_from_files.py` loads states, counties, and tracts
- `load_all_acs.py` loads states and counties only and is kept for testing/fallback scenarios
- the documented product flow assumes the tract-capable bulk-file path

## Commands

```bash
# Full stack
docker compose up --build -d

# Rebuild app only
docker compose up --build -d data-agent

# Logs
docker compose logs -f data-agent
docker compose logs mindsdb --tail 100

# Teardown
docker compose down
docker compose down -v

# Direct SQL through MindsDB
docker compose exec data-agent python3 -c "
import mindsdb_sdk
s = mindsdb_sdk.connect('http://mindsdb:47334')
print(s.query('SELECT * FROM census_db.acs_b01001 LIMIT 5').fetch())
"

# Direct PostgreSQL check
docker compose exec postgres psql -U census_admin -d census_data -c "SELECT count(*) FROM geographies"

# Run e2e tests
docker compose exec data-agent pytest tests/test_e2e.py -v --timeout=180
```

## Important Environment Variables

- `OPENAI_API_KEY`
- `CENSUS_API_KEY`
- `MINDSDB_HOST`
- `LLM_MODEL`
- `STREAM_THINKING`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`

Note:

- raw code fallback for `LLM_MODEL` is `gpt-4.1`
- Docker compose currently sets `LLM_MODEL` default to `gpt-4o`

## MindsDB SQL Pitfalls

These are current repo conventions and guardrails reflected in the prompt/docs:

- Avoid `IN (...) AND ...` patterns; use `OR` chains instead
- Product docs assume the bulk-file ETL path; the API loader is a testing-only fallback with narrower coverage
- Treat MindsDB as the required query layer; do not bypass it with direct runtime SQL clients

## Testing

Tests are full-stack e2e tests in `tests/test_e2e.py`.

- `tests/conftest.py` skips the suite if the Docker stack is unavailable
- tests call `query_agent()` directly
- tests are Census-specific and use heuristic output validation

## Documentation Policy For This Repo

- Keep docs aligned to the built product, not planned futures
- Prefer `docs/analysis/2026-03-07-current-implementation-design.md` as the source-of-truth design doc
- If behavior changes, update docs to reflect code, not aspiration
- Historical docs should be clearly labeled as historical rationale, not current architecture
