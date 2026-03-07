# Current Implementation Design

```mermaid
mindmap
  root((Census Insights))
    Runtime
      Chainlit UI
      OpenAI tool-calling agent
      MindsDB SQL proxy
      PostgreSQL storage
    Agent core
      query_agent_stream
      search_catalog
      sql_query
      export_csv
    Catalog and data
      acs_catalog metadata
      geographies
      acs_* tables
    Boundaries
      data-agent is custom code
      API loader is testing fallback
      legacy artifacts stay tracked
```

This opening map exists to anchor the reader in the product's fixed constraints before any flow detail appears. The repo is easier to understand as one narrow runtime path plus a small set of historical leftovers than as a long inventory of files.

## System structure

```mermaid
classDiagram
    class Browser
    class ChainlitApp
    class AgentLoop
    class OpenAIAPI
    class MindsDB
    class PostgreSQL
    class CsvExports

    Browser --> ChainlitApp : chat session
    ChainlitApp --> AgentLoop : message handling
    AgentLoop --> OpenAIAPI : tool-calling completions
    AgentLoop --> MindsDB : read-only SQL tools
    MindsDB --> PostgreSQL : query execution
    AgentLoop --> CsvExports : export_csv
    CsvExports --> ChainlitApp : downloadable file
```

This structure matters because only the Chainlit app and the agent loop are product code; everything else is an integration boundary. Keeping MindsDB in the middle preserves the main architectural constraint: the app must reason through the proxy layer rather than bypass it with direct runtime database clients.

## Startup flow

```mermaid
flowchart TD
    DC[docker compose up] --> DA[data-agent container]
    DA --> EP[scripts/entrypoint.sh]
    EP --> Q{geographies has rows?}
    Q -- yes --> SM[run setup_mindsdb.py]
    Q -- no --> LF[run load_from_files.py]
    LF --> SM
    SM --> CL[chainlit run src/app.py]
```

This is the product bootstrap path because the shipped design assumes tract-capable data is available locally and should not be reloaded once populated. The entrypoint still contains a `CENSUS_API_KEY`-based fallback loader in code, but that path exists for testing and is intentionally not part of the main architecture narrative.

## Request lifecycle

```mermaid
sequenceDiagram
    participant User
    participant App as Chainlit app
    participant Agent as Agent loop
    participant OpenAI as OpenAI API
    participant MindsDB
    participant PG as PostgreSQL

    User->>App: send question
    App->>Agent: query_agent_stream
    Agent->>OpenAI: completion request with tools
    OpenAI-->>Agent: tool call or final answer

    alt tool call
        Agent->>MindsDB: search_catalog / sql_query / export_csv
        MindsDB->>PG: execute SQL
        PG-->>MindsDB: result set
        MindsDB-->>Agent: tool result
        Agent->>OpenAI: append tool output
        OpenAI-->>Agent: next tool call or final answer
    end

    Agent-->>App: answer + optional export metadata
    App-->>User: final response and downloads
```

This interaction pattern exists because the model is the planner while the application stays the executor. The loop keeps planning, SQL execution, and user-visible output separated so retries and tool errors can be handled without collapsing the entire conversation path.

## Example happy path

```mermaid
sequenceDiagram
    participant User
    participant App as Chainlit app
    participant Agent as Agent loop
    participant OpenAI as OpenAI API
    participant MindsDB
    participant PG as PostgreSQL

    User->>App: "What are the top 10 counties in Georgia by median household income in 2023?"
    App->>Agent: query_agent_stream
    Agent->>OpenAI: completion request with tools
    OpenAI-->>Agent: search_catalog(query="median household income")
    Agent->>MindsDB: query census_db.acs_catalog
    MindsDB->>PG: SELECT table_id table_title variable_id label ...
    PG-->>MindsDB: B19013 / B19013_001E rows
    MindsDB-->>Agent: CSV catalog matches
    Agent->>OpenAI: tool result with B19013 metadata
    OpenAI-->>Agent: sql_query(SELECT g.name, t.b19013_001e ...)
    Agent->>MindsDB: query census_db.acs_b19013 join census_db.geographies
    MindsDB->>PG: top-10 county SQL for Georgia 2023
    PG-->>MindsDB: county names + income values
    MindsDB-->>Agent: CSV result rows
    Agent->>OpenAI: tool result with top-10 rows
    OpenAI-->>Agent: final summary answer
    Agent-->>App: answer
    App-->>User: ranked counties with median income
```

This example is worth drawing because it shows the intended product behavior in one pass: catalog first for schema discovery, MindsDB second for data retrieval, then a plain-language answer built from returned rows rather than guessed table knowledge.

## Streaming agent function map

```mermaid
classDiagram
    class app_py
    class query_agent_stream
    class _build_messages
    class _handle_tool_call
    class _execute_sql
    class _export_csv
    class _search_catalog

    app_py --> query_agent_stream : streaming mode
    query_agent_stream --> _build_messages : same message window
    query_agent_stream --> _handle_tool_call : execute model-selected tool
    _handle_tool_call --> _search_catalog : discovery
    _handle_tool_call --> _execute_sql : inline analytics
    _handle_tool_call --> _export_csv : file delivery
```

These functions deserve top billing because they are the actual product. The user experience is determined far more by message construction, tool dispatch, retries, and streaming than by how the database was initially loaded.

## Catalog search behavior

```mermaid
flowchart TD
    Q[search_catalog query] --> T[tokenize and lowercase]
    T --> F[drop stop words and one-character terms]
    F --> A[search distinct tables with AND matching on table_title or table_id]
    A --> O[if multi-term also search with OR fallback]
    O --> R[merge and score matches by title density title hits and id hits]
    R --> TOP[keep top 15 tables]
    TOP --> V[fetch variable rows from census_db.acs_catalog where is_estimate = TRUE]
    V --> C[group by table keep first 5 vars per table and cap at 75 rows]
    C --> CSV[return CSV with table_id table_title variable_id label]
```

This pipeline matters because catalog search is the agent's real source of truth for schema discovery. The runtime succeeds not by guessing ACS columns, but by narrowing a large metadata surface into a small CSV payload the model can reliably read before composing SQL.

## Catalog storage and query path

```mermaid
sequenceDiagram
    participant Model as OpenAI tool call
    participant Agent as _search_catalog
    participant MindsDB
    participant PG as PostgreSQL
    participant Catalog as acs_catalog

    Model->>Agent: search_catalog(query)
    Agent->>MindsDB: SELECT DISTINCT table_id table_title ... LIKE ...
    MindsDB->>PG: query census_db.acs_catalog
    PG->>Catalog: scan estimate metadata rows
    Catalog-->>PG: matching tables
    PG-->>MindsDB: ranked table candidates
    Agent->>MindsDB: SELECT table_id table_title variable_id label for top tables
    MindsDB->>PG: second catalog query
    PG-->>MindsDB: variable rows
    MindsDB-->>Agent: DataFrame results
    Agent-->>Model: CSV text
```

This split query path exists to keep discovery cheap and deterministic: table ranking first, variable extraction second. `scripts/init_db.sql` provisions FTS indexes on `table_title` and `label`, but the current agent intentionally uses simple `LIKE` matching and Python-side ranking so the model sees stable, explicit results.

## Agent decision flow

```mermaid
flowchart TD
    S[Build messages from system prompt, last 50 turns, and new question] --> OAI[OpenAI completion]
    OAI --> FR{finish_reason}
    FR -- stop --> DONE[return final answer]
    FR -- tool_calls --> TC[execute tool calls]
    TC --> RES{sql_query or export_csv returned error or empty result?}
    RES -- yes --> TEMP[raise temperature to 0.3 and increment consecutive_errors]
    RES -- no --> RESET[reset temperature and continue]
    TEMP --> LIMIT{3 failed SQL/export iterations?}
    LIMIT -- yes --> FAIL[return failure message]
    LIMIT -- no --> OAI
    RESET --> OAI
```

This flow is intentionally conservative because the product needs bounded recovery rather than open-ended agent wandering. The retry logic only reacts to SQL and export outcomes so catalog discovery can remain cheap while the expensive failure modes stay tightly capped.

## SQL guardrails and limits

```mermaid
flowchart TD
    SQL[sql_query or export_csv input] --> CHECK[_check_dml]
    CHECK --> SAFE{contains DML DDL or internal semicolon?}
    SAFE -- yes --> REJECT[return error immediately]
    SAFE -- no --> RUN[execute through MindsDB]
    RUN --> RESULT{rows returned?}
    RESULT -- no --> EMPTY[return empty-result hint]
    RESULT -- yes --> SIZE{query or export path?}
    SIZE -- sql_query --> CAP1[cap at 500 rows and append export hint]
    SIZE -- export_csv --> CAP2[cap at 100000 rows and write temp CSV]
    RUN --> FAIL{execution error?}
    FAIL -- yes --> CLASSIFY[_classify_sql_error with auto catalog hints]
```

These guardrails are central because the product promises useful analysis without handing the model unrestricted SQL power. The row caps, DML rejection, and classified error hints shape the agent's real behavior more than the load pipeline does.

## Tool structure

```mermaid
classDiagram
    class AgentLoop
    class SearchCatalogTool
    class SqlQueryTool
    class ExportCsvTool
    class ExportArtifact

    AgentLoop --> SearchCatalogTool : discover tables first
    AgentLoop --> SqlQueryTool : answer analytical questions
    AgentLoop --> ExportCsvTool : produce downloads
    ExportCsvTool --> ExportArtifact : writes temp CSV
```

This narrow tool surface exists to bound model behavior and keep every risky operation behind one safety layer. Three tools are enough to separate discovery, inline analysis, and file delivery without encouraging the model to improvise extra capabilities.

## Streaming runtime

```mermaid
stateDiagram-v2
    [*] --> AwaitingMessage
    AwaitingMessage --> StreamingRun: incoming user message
    StreamingRun --> EmitSteps: tool and output events
    EmitSteps --> PersistHistory: final answer ready
    PersistHistory --> AwaitingMessage
```

The documented product runtime is streaming because that is the user-facing path the app is intended to operate with. Step emission is part of the product behavior, not an optional extra in the design narrative.

## Data model

```mermaid
erDiagram
    geographies ||--o{ acs_star_tables : geo_id

    geographies {
        varchar geo_id PK
        varchar name
        varchar sumlevel
        varchar state_fips
        varchar county_fips
        varchar tract_code
        varchar state_name
        varchar county_name
        varchar area_type
    }

    acs_catalog {
        varchar table_id
        varchar variable_id PK
        text label
        text table_title
        text universe
        boolean is_estimate
    }

    acs_star_tables {
        varchar geo_id PK
        integer data_year PK
        numeric variable_columns
    }
```

This schema favors one ACS table per table group because the agent can discover intent from catalog metadata instead of relying on a hand-curated semantic layer. The ER node labeled `acs_star_tables` is conceptual shorthand for the many auto-created `acs_<table_id>` tables rather than one literal table, which keeps the diagram aligned with the actual schema in `scripts/init_db.sql`.

## Primary data load

```mermaid
flowchart LR
    BULK[load_from_files.py] --> B1[read variables.json]
    BULK --> B2[parse local geographies]
    BULK --> B3[scan local .dat payloads]
    BULK --> B4[create acs_* tables]
    BULK --> B5[load states counties and tracts]
```

This is the documented load path because it is the only path that matches the product's tract-aware behavior. A narrower API-based loader remains in the codebase as a testing fallback, but it is intentionally outside the primary design story.

## Geography guarantee

```mermaid
flowchart TD
    BULK[bulk-file runtime dataset] --> GEO[states counties and tracts are loaded]
    GEO --> TRACTS[tract-level questions are part of the supported product flow]
```

This guarantee is shown explicitly because tract queries are part of the product contract, not an edge capability. The testing-only API loader is omitted here so the supported runtime story stays aligned with the intended deployment.

## Repository structure

```mermaid
classDiagram
    class Source
    class Scripts
    class Tests
    class Docs
    class PublicAssets

    Scripts --> Source : bootstraps and feeds runtime
    Tests --> Source : verifies behavior
    Docs --> Source : explains behavior
    PublicAssets --> Source : supports UI presentation
```

This structural view exists to emphasize that the repo is small but layered: runtime code, operational scripts, verification, and explanation each have a separate home. That separation keeps the built product maintainable even though ETL, app logic, and UI assets coexist in one repository.

## Active and historical artifacts

```mermaid
classDiagram
    class ActiveRuntime
    class TestingFallback
    class HistoricalArtifacts
    class RuntimeTruth

    RuntimeTruth --> ActiveRuntime : src/app.py
    RuntimeTruth --> ActiveRuntime : src/agent_client.py
    RuntimeTruth --> ActiveRuntime : scripts/entrypoint.sh
    RuntimeTruth --> ActiveRuntime : scripts/setup_mindsdb.py
    RuntimeTruth --> ActiveRuntime : scripts/load_from_files.py
    RuntimeTruth --> TestingFallback : scripts/load_all_acs.py
    RuntimeTruth --> HistoricalArtifacts : scripts/load_census_data.py
    RuntimeTruth --> HistoricalArtifacts : scripts/seed_data.sql
```

This split is drawn explicitly because the repo mixes product runtime files, testing fallback code, and older artifacts in one tree. Without that boundary, readers can easily mistake support code for the primary architecture.

## Source-of-truth files

```mermaid
classDiagram
    class CurrentBehavior
    class AppEntry
    class AgentCore
    class Bootstrap
    class MindsDBSetup
    class BulkETL
    class TestFallback
    class Schema
    class Compose
    class E2ETests

    CurrentBehavior --> AppEntry : src/app.py
    CurrentBehavior --> AgentCore : src/agent_client.py
    CurrentBehavior --> Bootstrap : scripts/entrypoint.sh
    CurrentBehavior --> MindsDBSetup : scripts/setup_mindsdb.py
    CurrentBehavior --> BulkETL : scripts/load_from_files.py
    CurrentBehavior --> Schema : scripts/init_db.sql
    CurrentBehavior --> Compose : docker-compose.yml
    CurrentBehavior --> E2ETests : tests/test_e2e.py
    CurrentBehavior --> TestFallback : load_all_acs.py for testing
```

This hierarchy matters because code must outrank narrative when the two disagree. The doc is useful only if it points maintainers back to the exact files that define runtime truth.
