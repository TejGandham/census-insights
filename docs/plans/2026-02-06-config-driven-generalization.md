# Config-Driven Generalization — Design Document

**Date:** 2026-02-06
**Status:** Draft — pending review
**Goal:** Extract all census-specific hardcoding into YAML configuration so the framework works with any data store plugged into MindsDB.

---

## 1. Current State: Census Hardcoding Inventory

**Verified against actual code** (not design docs). 43 distinct census-specific hardcodings across 10 files.

### 1.1 Agent Core — `src/agent_client.py` (574 lines)

**SYSTEM_PROMPT (lines 30-64) — 12 hardcodings:**

| Line | Hardcoded Value | What It Assumes |
|------|----------------|-----------------|
| 31 | `"US Census ACS 5-Year data (2019-2023)"` | Census domain, year range |
| 34-35 | `"~1,193 ACS 5-Year data tables covering demographics, economics..."` | Census table count and topics |
| 36-37 | `"census_db.acs_<table_id>"` and `"census_db.acs_b01001"` | Database name `census_db`, table prefix `acs_`, example table ID |
| 43 | `"b01001_001e"` | Census variable ID format for column names |
| 44 | `"geo_id"` and `"data_year"` | Census-specific join/filter columns |
| 46-49 | `"census_db.geographies"`, columns: `name, state_name, county_name, area_type, state_fips, county_fips, sumlevel` | Census geography table and schema |
| 51 | `"data_year = 2023"`, `"2019-2023"` | Year filter column, range, default |
| 52 | `"area_type = 'state'"`, `"'county'"` | Census geography type values |
| 53-55 | `"g.state_name = 'Texas'"`, `"g.state_name = 'California'"` | Census-specific SQL example |

**Tool descriptions (lines 66-137) — 5 hardcodings:**

| Line | Hardcoded Value |
|------|----------------|
| 71 | `"Search the ACS data catalog"` |
| 80 | `"ACS table titles and variable labels (e.g., 'race', 'sex by age', 'median household income', 'B19013')"` |
| 93 | `"Execute a read-only SQL query against the census database"` |
| 96 | `"census_db.acs_<table_id> (e.g., census_db.acs_b01001)"` |
| 97 | `"census_db.geographies"` |

Note: `EXPORT_CSV_TOOL` (lines 112-137) is **already generic** — no Census language.

**Catalog functions (lines 177-303) — 7 hardcodings:**

| Line | Code | Census-Specific Part |
|------|------|---------------------|
| 190 | `FROM census_db.acs_catalog` | Table name |
| 191 | `WHERE is_estimate = TRUE` | Census estimate-vs-MOE filter |
| 256 | `FROM census_db.acs_catalog` | Table name (again) |
| 257 | `WHERE is_estimate = TRUE` | Filter (again) |
| 282 | `r'census_db\.acs_(\w+)'` | Regex for table ID extraction |
| 289-292 | `FROM census_db.acs_catalog WHERE is_estimate = TRUE AND table_id = ...` | Table name + filter (again) |

**Error hints (line 321) — 1 hardcoding:**

| Line | Code |
|------|------|
| 321 | `"e.g., census_db.acs_b01001"` in table-not-found hint |

**What's already generic in agent_client.py (no changes needed):**
- `_check_dml()` (lines 160-174) — keyword DML guard
- `_execute_sql()` (lines 335-357) — runs any SELECT via MindsDB
- `_export_csv()` (lines 360-394) — writes any DataFrame to CSV
- `_handle_tool_call()` (lines 397-426) — dispatches by tool name
- `_build_messages()` (lines 429-437) — generic message assembly (only uses `SYSTEM_PROMPT` variable)
- `query_agent()` (lines 446-496) — tool loop with temperature ramp
- `query_agent_stream()` (lines 499-573) — streaming variant
- `_is_error_or_empty()` (lines 440-443) — error detection
- Constants: `MAX_HISTORY_TURNS=50`, `MAX_QUERY_ROWS=500`, `MAX_EXPORT_ROWS=100_000`
- `TOOLS` list at line 139 — static but easily made dynamic
- Stop words (lines 20-28) — generic English, comment on line 19 mentions "Census metadata" but the words themselves are language-generic

### 1.2 Chat UI — `src/app.py` (118 lines)

| Line | Hardcoded Value |
|------|----------------|
| 24 | `"What are the top 10 most populated states?"` |
| 28 | `"Which counties have the highest poverty rates?"` |
| 32 | `"Compare education levels in Texas vs California counties"` |
| 36 | `"How has median household income changed from 2019 to 2023 for the top 5 states?"` |

**Everything else in app.py is generic:** Chainlit lifecycle, session history, streaming/blocking dispatch, file export handling.

### 1.3 MindsDB Setup — `scripts/setup_mindsdb.py` (76 lines)

| Line | Hardcoded Value |
|------|----------------|
| 20 | `PG_USER` default: `"census_admin"` |
| 21 | `PG_PASS` default: `"census_pass"` |
| 24 | `PG_DB` default: `"census_data"` |
| 45 | `"Creating PostgreSQL connection (census_db)..."` |
| 47 | `server.drop_database("census_db")` — **hardcoded string, not from env var** |
| 52 | `server.create_database("census_db", ...)` — **hardcoded string, not from env var** |
| 62 | `print("  census_db connected.")` |
| 65 | Old view cleanup list: `("demographics", "economics", "housing", "education", "census_data")` |
| 71 | `"Agent queries census_db.acs_* tables directly."` |

**What's already generic:** `wait_for_mindsdb()` (lines 27-35), connection logic (lines 39-61 structure).

### 1.4 Bootstrap — `scripts/entrypoint.sh` (43 lines)

| Line | Hardcoded Value |
|------|----------------|
| 14 | `user` default: `'census_admin'` |
| 15 | `password` default: `'census_pass'` |
| 16 | `dbname` default: `'census_data'` |
| 18 | `'SELECT count(*) FROM geographies'` — bootstrap check table |
| 28-29 | `CENSUS_API_KEY` env var name |
| 33 | `python scripts/load_all_acs.py` — Census ETL script |

### 1.5 Docker & Env — `docker-compose.yml`, `.env`, `.env.example`

| File | Line | Hardcoded Value |
|------|------|----------------|
| docker-compose.yml | 5-7 | Defaults: `census_admin`, `census_pass`, `census_data` |
| docker-compose.yml | 39 | `CENSUS_API_KEY` env var |
| docker-compose.yml | 43-45 | Same defaults repeated for data-agent |
| .env | 2-4 | `census_admin`, `census_pass`, `census_data` |
| .env | 5 | Actual Census API key (exposed in repo — security note) |
| .env.example | 2-5 | Same Census defaults as documentation |

### 1.6 Entirely Census-Specific Files (no generalization — keep as-is)

| File | Lines | Why It Stays |
|------|-------|-------------|
| `scripts/init_db.sql` | 104 | Creates `geographies` table with Census sumlevel logic, `acs_catalog` with Census variable structure, drops Census-specific old tables. Each data store brings its own schema. |
| `scripts/load_all_acs.py` | 643 | Census API client: fetches `variables.json`, parses E/M suffixes, constructs `0400000US`/`0500000US` geo_ids, batches API calls. 100% Census. |
| `scripts/load_census_data.py` | ~230 | Deprecated legacy ETL. Candidate for deletion. |
| `scripts/seed_data.sql` | ~370 | Hand-curated Census sample data. Census-only. |
| `tests/test_e2e.py` | 403 | 8 tests asking Census questions. Assertion helpers (lines 19-110) are generic. |

---

## 2. Design: YAML Configuration

### 2.1 File Location and Loading

```
config/
  datastore.yml              # Active config (loaded at startup)
  examples/
    census.yml               # Census reference config (copy to datastore.yml)
    sales.yml                # Example: simple flat-table data store
```

Env var `DATASTORE_CONFIG` overrides the path (default: `config/datastore.yml`).

**Dependency:** Add `pyyaml` to `requirements.txt` (currently not present).

### 2.2 Full Config Schema

```yaml
# ---------------------------------------------------------------
# Data store configuration for the MindsDB text-to-SQL agent.
# One file per deployment. Copy an example and customize.
# ---------------------------------------------------------------

# Display name (shown in logs, not sent to LLM)
name: "US Census ACS 5-Year Data"

# MindsDB database connection name.
# setup_mindsdb.py creates this; agent_client.py queries through it.
mindsdb_database: "census_db"

# ---------------------------------------------------------------
# System prompt — the full text sent as the LLM system message.
# This is the primary knob: it defines the LLM's identity, schema
# knowledge, workflow, and rules for this data store.
# ---------------------------------------------------------------
system_prompt: |
  You are a data analyst for US Census ACS 5-Year data (2019-2023).
  Answer questions clearly and concisely.

  ## Database
  The database has ~1,193 ACS 5-Year data tables covering demographics,
  economics, housing, education, health insurance, commuting, language,
  ancestry, and more. Tables are accessed through MindsDB as
  `census_db.acs_<table_id>` where table_id is lowercase
  (e.g., `census_db.acs_b01001`).

  ## Workflow
  1. ALWAYS start by calling `search_catalog` to find the right table
     and column names. Never guess — the catalog is the source of truth.
  2. Use the catalog results to identify the table_id and variable_id
     columns you need. Search with short topic keywords (e.g., 'race',
     'median household income'), not full sentences.
  3. Call `sql_query` to query the data. Column names are lowercase
     variable IDs (e.g., `b01001_001e`). Every table also has `geo_id`
     and `data_year` columns.

  ## Geographies
  Join with geographies for location info:
    `JOIN census_db.geographies g ON t.geo_id = g.geo_id`
  Columns: name, state_name, county_name, area_type ('state' or 'county'),
  state_fips, county_fips, sumlevel.

  ## Rules
  - Filter by year with `data_year = 2023`. Default year: 2023
    (data available 2019-2023).
  - Use `area_type = 'state'` for states, `'county'` for counties.
  - IMPORTANT: Never use IN (...) with AND. Instead use OR.
    Example: `(g.state_name = 'Texas' OR g.state_name = 'California')`
    not `g.state_name IN ('Texas', 'California')`.
  - For comparisons, use GROUP BY with AVG/SUM/COUNT.
  - Round numbers appropriately. Format currency with $ signs.
  - Present data in clear tables when appropriate.
  - When the user asks for CSV, a download, all rows, or large data
    exports, use the `export_csv` tool instead of `sql_query`.
  - For analytical questions (top 10, comparisons, averages), use
    sql_query as normal.
  - Never execute DML (INSERT, UPDATE, DELETE, DROP).

# ---------------------------------------------------------------
# Catalog — metadata table the LLM searches to discover tables.
# Set enabled: false for data stores with few self-documenting tables.
# When disabled, the search_catalog tool is NOT registered with
# the LLM, and schema knowledge comes from the system_prompt alone.
# ---------------------------------------------------------------
catalog:
  enabled: true
  table: "acs_catalog"             # queried as {mindsdb_database}.{table}
  columns:
    table_id: "table_id"           # groups variables into tables
    variable_id: "variable_id"     # individual column/variable name
    label: "label"                 # human-readable description
    table_title: "table_title"     # human-readable table name
  # WHERE clauses always appended to catalog queries.
  # Census uses this to filter out margin-of-error rows.
  # Omit or leave empty for no extra filtering.
  filters:
    - "is_estimate = TRUE"
  # Regex to extract a table_id from SQL the LLM writes.
  # Used for auto-lookup when a SQL error references a table.
  # Group 1 must capture the table_id (uppercased by the code).
  table_id_regex: 'census_db\.acs_(\w+)'

# ---------------------------------------------------------------
# Tool descriptions — text the LLM sees in the function schema.
# Lets each data store describe its tools in domain-appropriate terms.
# ---------------------------------------------------------------
tool_descriptions:
  search_catalog: >
    Search the ACS data catalog to find which tables contain the data
    you need. Returns matching table IDs, variable names, and
    descriptions. ALWAYS call this first before querying data tables.
  search_catalog_param: >
    Search terms matching ACS table titles and variable labels
    (e.g., 'race', 'sex by age', 'median household income', 'B19013').
    Use short Census topic keywords, not full sentences.
  sql_query: >
    Execute a read-only SQL query against the census database through
    MindsDB. Returns the result set as CSV text. Best for small/medium
    results (analytical queries, top-N, aggregations).
    Tables are accessed as census_db.acs_<table_id>
    (e.g., census_db.acs_b01001).
    Join with census_db.geographies for location names.

# ---------------------------------------------------------------
# Error hints — appended to structured error messages.
# ---------------------------------------------------------------
error_hints:
  table_not_found: >
    The table may not exist. Check the table_id format
    (lowercase in SQL, e.g., census_db.acs_b01001).

# ---------------------------------------------------------------
# UI starter questions — shown in the Chainlit chat home screen.
# ---------------------------------------------------------------
starter_questions:
  - label: "Top populated states"
    message: "What are the top 10 most populated states?"
  - label: "Highest poverty rates"
    message: "Which counties have the highest poverty rates?"
  - label: "Education comparison"
    message: "Compare education levels in Texas vs California counties"
  - label: "Income trends"
    message: "How has median household income changed from 2019 to 2023 for the top 5 states?"

# ---------------------------------------------------------------
# Bootstrap — controls entrypoint.sh behavior.
# ---------------------------------------------------------------
bootstrap:
  check_table: "geographies"
  required_env_vars:
    - "CENSUS_API_KEY"
  etl_script: "scripts/load_all_acs.py"
```

### 2.3 No-Catalog Example (Flat Tables)

```yaml
# config/examples/sales.yml — small data store, no catalog needed

name: "Retail Sales Database"
mindsdb_database: "sales_db"

catalog:
  enabled: false

system_prompt: |
  You are a data analyst for a retail sales database.
  Answer questions clearly and concisely.

  ## Available Tables
  - `sales_db.orders` (order_id, customer_id, order_date, total, status)
  - `sales_db.customers` (customer_id, name, email, state, signup_date)
  - `sales_db.products` (product_id, name, category, price)
  - `sales_db.order_items` (order_item_id, order_id, product_id, qty, unit_price)

  ## Joins
  - orders → customers ON customer_id
  - order_items → orders ON order_id
  - order_items → products ON product_id

  ## Rules
  - Use sql_query for analytical questions. Use export_csv for large exports.
  - Never execute DML (INSERT, UPDATE, DELETE, DROP).

tool_descriptions:
  sql_query: >
    Execute a read-only SQL query against the sales database through
    MindsDB. Returns the result set as CSV text.

starter_questions:
  - label: "Top customers"
    message: "Who are our top 10 customers by total spend?"
  - label: "Monthly revenue"
    message: "What is the monthly revenue trend for the past year?"

bootstrap:
  check_table: "orders"
  required_env_vars: []
  etl_script: "scripts/load_sales.py"
```

When `catalog.enabled: false`:
- `search_catalog` tool is **not registered** with the LLM — it never appears in the tools list
- The LLM gets full schema in the system prompt and goes directly to `sql_query`
- Zero extra tool calls, zero latency overhead
- All catalog functions (`_search_catalog`, `_search_catalog_tables`, `_auto_search_catalog`, `_extract_table_id`) are skipped entirely

This is the right approach because:
- For 3-5 tables, the schema fits in ~500 tokens of system prompt — the LLM reasons perfectly from inline context
- A `search_catalog` tool that always returns the same static list wastes a tool-call round-trip (~1-2s latency) on every question
- The LLM never sees a tool it doesn't need, so it can't misuse it

---

## 3. Code Changes — File by File

### 3.1 New: `src/config.py` (~35 lines)

```python
"""Data store configuration loader."""

import os
from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "datastore.yml"
_config = None


def load_config() -> dict:
    global _config
    if _config is not None:
        return _config

    path = os.getenv("DATASTORE_CONFIG", str(_DEFAULT_PATH))
    with open(path) as f:
        _config = yaml.safe_load(f)

    # Required fields
    for key in ("name", "mindsdb_database", "system_prompt"):
        if key not in _config:
            raise ValueError(f"Config missing required key: {key}")

    # Defaults
    _config.setdefault("catalog", {"enabled": False})
    _config.setdefault("starter_questions", [])
    _config.setdefault("tool_descriptions", {})
    _config.setdefault("error_hints", {})
    _config.setdefault("bootstrap", {})

    return _config
```

### 3.2 Modified: `src/agent_client.py`

**Change 1 — Replace `SYSTEM_PROMPT` constant (line 30-64):**

```python
# BEFORE:
SYSTEM_PROMPT = "You are a data analyst for US Census ACS 5-Year data..."

# AFTER:
from config import load_config

# SYSTEM_PROMPT removed. Retrieved from config at runtime.
```

**Change 2 — Replace hardcoded tool dicts (lines 66-139) with builders:**

```python
def _build_search_catalog_tool() -> dict:
    cfg = load_config()
    descs = cfg.get("tool_descriptions", {})
    return {
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": descs.get("search_catalog",
                "Search the data catalog to find relevant tables and columns."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": descs.get("search_catalog_param",
                            "Search terms for table and column names."),
                    }
                },
                "required": ["query"],
            },
        },
    }


def _build_sql_tool() -> dict:
    cfg = load_config()
    descs = cfg.get("tool_descriptions", {})
    return {
        "type": "function",
        "function": {
            "name": "sql_query",
            "description": descs.get("sql_query",
                "Execute a read-only SQL query through MindsDB. Returns CSV text."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A SELECT SQL query to execute.",
                    }
                },
                "required": ["query"],
            },
        },
    }


# EXPORT_CSV_TOOL stays as-is (already generic, lines 112-137)

def _build_tools() -> list[dict]:
    cfg = load_config()
    tools = []
    if cfg["catalog"]["enabled"]:
        tools.append(_build_search_catalog_tool())
    tools.append(_build_sql_tool())
    tools.append(EXPORT_CSV_TOOL)
    return tools
```

**Change 3 — Parameterize catalog functions (lines 177-303):**

6 substitutions total. Pattern for each:

```python
# BEFORE (line 190):
FROM census_db.acs_catalog

# AFTER:
cfg = load_config()
db = cfg["mindsdb_database"]
cat = cfg["catalog"]
cat_table = f"{db}.{cat['table']}"
# ... FROM {cat_table}
```

```python
# BEFORE (line 191):
WHERE is_estimate = TRUE AND ({where})

# AFTER:
filters = cat.get("filters", [])
filter_clause = " AND ".join(filters) if filters else "TRUE"
# ... WHERE {filter_clause} AND ({where})
```

```python
# BEFORE (line 282):
r'census_db\.acs_(\w+)'

# AFTER:
pattern = cfg["catalog"].get("table_id_regex")
if not pattern:
    return None
```

Column references (`table_id`, `table_title`, `variable_id`, `label`) come from `cfg["catalog"]["columns"]` dict instead of hardcoded strings.

**Change 4 — Error hints (line 321):**

```python
# BEFORE:
hint = "The table may not exist. Check the table_id format (lowercase in SQL, e.g., census_db.acs_b01001)."

# AFTER:
hints = load_config().get("error_hints", {})
hint = hints.get("table_not_found", "The table may not exist. Check the table name format.")
```

**Change 5 — `_build_messages` (line 431):**

```python
# BEFORE:
messages = [{"role": "system", "content": SYSTEM_PROMPT}]

# AFTER:
messages = [{"role": "system", "content": load_config()["system_prompt"]}]
```

**Change 6 — `query_agent` and `query_agent_stream` use dynamic tools:**

```python
# BEFORE (line 460):
tools=TOOLS,

# AFTER:
tools=_build_tools(),
```

Same change in `query_agent_stream` (around line 512).

**Change 7 — Guard catalog calls in `_handle_tool_call` (line 401):**

```python
if tc.function.name == "search_catalog":
    cfg = load_config()
    if not cfg["catalog"]["enabled"]:
        return "No catalog available for this data store.", None
    result = _search_catalog(args["query"])
    return result, None
```

This is a safety net — if `catalog.enabled: false`, the tool shouldn't be registered at all, so the LLM shouldn't call it. But defensive coding is free.

### 3.3 Modified: `src/app.py`

**Replace starter questions (lines 19-38):**

```python
# BEFORE:
@cl.set_starters
async def starters():
    return [
        cl.Starter(label="Top populated states", message="What are the top 10..."),
        # ...3 more hardcoded starters
    ]

# AFTER:
from config import load_config

@cl.set_starters
async def starters():
    cfg = load_config()
    questions = cfg.get("starter_questions", [])
    if not questions:
        return []
    return [
        cl.Starter(label=q["label"], message=q["message"])
        for q in questions
    ]
```

### 3.4 Modified: `scripts/setup_mindsdb.py`

**Replace hardcoded `"census_db"` (lines 45-62):**

```python
# BEFORE (line 47):
server.drop_database("census_db")
# BEFORE (line 52):
server.create_database("census_db", ...)

# AFTER:
DB_NAME = os.getenv("MINDSDB_DATABASE_NAME", "census_db")
# ...
server.drop_database(DB_NAME)
server.create_database(DB_NAME, ...)
```

**Remove old view cleanup (line 65):** This is a legacy migration artifact. The current architecture doesn't use views. Remove or make it a no-op.

### 3.5 Modified: `scripts/entrypoint.sh`

**Parameterize with env vars (backward-compatible defaults):**

```bash
# BEFORE (line 18):
cur.execute('SELECT count(*) FROM geographies')

# AFTER:
CHECK_TABLE = os.getenv('BOOTSTRAP_CHECK_TABLE', 'geographies')
cur.execute(f'SELECT count(*) FROM {CHECK_TABLE}')
```

```bash
# BEFORE (line 28):
if [ -z "$CENSUS_API_KEY" ]; then

# AFTER:
REQUIRED_KEY="${BOOTSTRAP_REQUIRED_KEY:-CENSUS_API_KEY}"
eval KEY_VALUE=\$$REQUIRED_KEY
if [ -z "$KEY_VALUE" ]; then
```

```bash
# BEFORE (line 33):
python scripts/load_all_acs.py

# AFTER:
ETL_SCRIPT="${BOOTSTRAP_ETL_SCRIPT:-scripts/load_all_acs.py}"
python "$ETL_SCRIPT"
```

### 3.6 Modified: `docker-compose.yml`

Add new env vars to data-agent service (defaults preserve current behavior):

```yaml
data-agent:
  environment:
    # ... existing vars unchanged ...
    DATASTORE_CONFIG: ${DATASTORE_CONFIG:-config/datastore.yml}
    MINDSDB_DATABASE_NAME: ${MINDSDB_DATABASE_NAME:-census_db}
    BOOTSTRAP_CHECK_TABLE: ${BOOTSTRAP_CHECK_TABLE:-geographies}
    BOOTSTRAP_ETL_SCRIPT: ${BOOTSTRAP_ETL_SCRIPT:-scripts/load_all_acs.py}
    BOOTSTRAP_REQUIRED_KEY: ${BOOTSTRAP_REQUIRED_KEY:-CENSUS_API_KEY}
```

### 3.7 Modified: `requirements.txt`

Add one dependency:

```
pyyaml
```

---

## 4. What Does NOT Change

| File / Component | Reason |
|-----------------|--------|
| `_check_dml()` | Generic DML guard — no Census references |
| `_execute_sql()` | Runs any SELECT via MindsDB — no Census references |
| `_export_csv()` | Writes any DataFrame to CSV — no Census references |
| `EXPORT_CSV_TOOL` | Already generic tool description |
| `_handle_tool_call()` dispatch | Generic tool routing (except adding catalog guard) |
| `query_agent()` tool loop | Generic: iterate tool calls, temperature ramp, error counting |
| `query_agent_stream()` | Generic streaming variant of above |
| `_build_messages()` structure | Generic: system + history + question (only prompt text changes) |
| `_is_error_or_empty()` | Generic error detection |
| `app.py` streaming/blocking handlers | Generic Chainlit lifecycle |
| `conftest.py` | Generic: just checks `_get_server().query("SELECT 1")` |
| `_SEARCH_STOP_WORDS` | Generic English stop words (comment mentions Census but words are universal) |
| `scripts/init_db.sql` | 100% Census — each data store brings its own schema SQL |
| `scripts/load_all_acs.py` | 100% Census — each data store brings its own ETL |
| `scripts/seed_data.sql` | 100% Census |
| `scripts/load_census_data.py` | Deprecated — candidate for deletion |
| `tests/test_e2e.py` | Census-specific tests (assertion helpers are generic) |

---

## 5. Migration Phases

### Phase 1: Config Loader + Prompt + Starters

**Files touched:** new `src/config.py`, new `config/datastore.yml`, edit `src/agent_client.py` (replace SYSTEM_PROMPT), edit `src/app.py` (replace starters), edit `requirements.txt` (add pyyaml).

**What changes:** System prompt and starter questions load from YAML instead of Python constants. Everything else stays hardcoded.

**Validation:** `docker compose up` works identically. Run e2e tests — all 8 pass unchanged.

**Risk:** Near zero. Config file contains the exact same text that was hardcoded. Single new dependency (pyyaml).

### Phase 2: Tool Descriptions + Conditional Catalog Registration

**Files touched:** edit `src/agent_client.py` (tool builders, dynamic TOOLS list, catalog guard).

**What changes:** Tool descriptions come from config. `search_catalog` only registered when `catalog.enabled: true`. Catalog is still present for Census — behavior unchanged.

**Validation:** Run e2e tests (Census config, catalog enabled). Create a test config with `catalog.enabled: false` and verify tools list has no `search_catalog`.

### Phase 3: Parameterize Catalog Functions

**Files touched:** edit `src/agent_client.py` (`_search_catalog_tables`, `_search_catalog`, `_auto_search_catalog`, `_extract_table_id`, `_classify_sql_error`).

**What changes:** Catalog table name, column names, filters, and regex come from config instead of hardcoded strings. 6 string substitutions across 4 functions.

**Validation:** Run e2e tests. Census catalog search behavior identical.

### Phase 4: Bootstrap + Docker

**Files touched:** edit `scripts/setup_mindsdb.py`, edit `scripts/entrypoint.sh`, edit `docker-compose.yml`, edit `.env.example`.

**What changes:** `MINDSDB_DATABASE_NAME` env var replaces hardcoded `"census_db"`. Entrypoint uses env vars for check table, ETL script, and required key. All have backward-compatible defaults.

**Validation:** `docker compose up` with no env changes works identically. Change `MINDSDB_DATABASE_NAME=test_db` and verify setup_mindsdb creates `test_db`.

---

## 6. Gap Assessment

| Area | Lines of Code to Change | Complexity |
|------|------------------------|------------|
| New `config.py` module | ~35 new lines | Low — YAML load + validation |
| System prompt from config | 1 line changed in `_build_messages` | Trivial |
| Starter questions from config | 5 lines changed in `app.py` | Trivial |
| Tool descriptions from config | ~50 new lines (builders), delete ~50 old lines | Low — template swap |
| Conditional tool registration | ~5 lines (build_tools + guard) | Trivial |
| Catalog function parameterization | ~20 lines changed across 4 functions | Medium — careful substitution |
| Error hints from config | ~3 lines changed | Trivial |
| setup_mindsdb.py env var | ~3 lines changed | Trivial |
| entrypoint.sh env vars | ~6 lines changed | Low |
| docker-compose.yml env vars | ~5 new lines | Trivial |
| YAML config file | ~80 new lines (content already known) | Low — copy existing strings |
| requirements.txt | 1 new line | Trivial |

**Total: ~90 lines of new code, ~30 lines modified, ~50 lines deleted (replaced by config).**

The framework's core logic (tool loop, SQL execution, streaming, CSV export, error handling, temperature ramp, DML guard, history management) is already fully generic. The census-specific parts are all **surface-level text** — strings in prompts, tool descriptions, table names, and SQL fragments. No structural refactoring is needed. No new abstractions, no new classes, no architecture changes.
