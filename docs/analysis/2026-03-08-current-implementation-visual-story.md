# Current Implementation Visual Story

This diagram-first companion retells the live product as one camera move: world, shape, motion, state, data, and risk.

It complements `docs/analysis/2026-03-07-current-implementation-design.md` rather than replacing it. If this document and the source-of-truth design doc ever drift, the code and the source-of-truth design doc win.

Across the diagrams, `ACS Table Group` is the diagram-safe stand-in for the runtime `acs_<table_id>` table family.

## 1. The World

We begin with the system in its environment. The `User` reaches the product through the `Browser`, while `data-agent` bridges the application runtime to the services and data sources around it.

```mermaid
C4Context
    title Census Insights in Its Runtime World

    Person(user, "User", "Asks Census questions and downloads results")
    System_Ext(browser, "Browser", "Loads the Chainlit chat UI on port 8001")
    System(dataAgent, "data-agent", "Custom application container that bootstraps data, runs Chainlit, and executes the OpenAI tool loop")
    System_Ext(openai, "OpenAI API", "Returns tool calls and final answers")
    System_Ext(mindsdb, "MindsDB", "SQL proxy layer reached as http://mindsdb:47334 inside the stack")
    System_Ext(postgres, "PostgreSQL", "Stores geographies, acs_catalog, and ACS Table Group tables")
    System_Ext(censusVars, "Census variables.json", "Metadata feed used to build acs_catalog")
    System_Ext(bulkFiles, "Local ACS Bulk Files", "Primary tract-capable ACS summary file source")

    Rel(user, browser, "Uses")
    Rel(browser, dataAgent, "Sends chat messages to")
    Rel(dataAgent, openai, "Requests tool-calling completions from")
    Rel(dataAgent, mindsdb, "Executes search_catalog, sql_query, and export_csv through")
    Rel(mindsdb, postgres, "Reads and queries")
    Rel(dataAgent, censusVars, "Loads acs_catalog from during bootstrap")
    Rel(dataAgent, bulkFiles, "Loads geographies and ACS Table Group tables from during bootstrap")
```

Now that `data-agent` is the box we care about, the next view opens it up. The `User`, `Browser`, `OpenAI API`, `MindsDB`, and `PostgreSQL` stay in frame so the inside of the system never loses its surroundings.

## 2. The Shape

The `User` from the context diagram still enters through the `Browser`, but the request now lands inside `data-agent`. This view shows the major runtime blocks that turn one container into a working product.

```mermaid
C4Container
    title data-agent Unpacked

    Person(user, "User", "Same User from the context diagram")
    System_Ext(browser, "Browser", "Same Browser from the context diagram")
    System_Ext(openai, "OpenAI API", "Same OpenAI API from the context diagram")
    System_Ext(mindsdb, "MindsDB", "Same MindsDB from the context diagram")
    System_Ext(postgres, "PostgreSQL", "Same PostgreSQL from the context diagram")
    System_Ext(censusVars, "Census variables.json", "Same metadata source from the context diagram")
    System_Ext(bulkFiles, "Local ACS Bulk Files", "Same bulk-file source from the context diagram")

    System_Boundary(dataAgentBoundary, "data-agent") {
        Container(chainlitApp, "Chainlit App", "src/app.py", "Owns chat sessions, streaming output, and file delivery")
        Container(agentLoop, "Agent Loop", "src/agent_client.py", "Builds messages, calls OpenAI API, and executes tools")
        Container(fileExport, "File Export", "temp CSV artifact", "Stores export_csv results until Chainlit serves them")
        Container(bootstrapPath, "Bootstrap Path", "scripts/entrypoint.sh", "Checks data, runs setup, and starts Chainlit")
        Container(bulkFileEtl, "Bulk File ETL", "scripts/load_from_files.py", "Loads acs_catalog, geographies, and ACS Table Group tables")
        Container(mindsdbSetup, "MindsDB Setup", "scripts/setup_mindsdb.py", "Creates the census_db connection in MindsDB")
    }

    Rel(user, browser, "Uses")
    Rel(browser, chainlitApp, "Sends question to")
    Rel(chainlitApp, agentLoop, "Passes question and history to")
    Rel(agentLoop, openai, "Requests completions from")
    Rel(agentLoop, mindsdb, "Runs search_catalog, sql_query, and export_csv through")
    Rel(agentLoop, fileExport, "Writes CSV artifacts to")
    Rel(bootstrapPath, bulkFileEtl, "Runs when geographies is empty")
    Rel(bootstrapPath, mindsdbSetup, "Runs before startup completes")
    Rel(bootstrapPath, chainlitApp, "Starts")
    Rel(bulkFileEtl, censusVars, "Fetches metadata from")
    Rel(bulkFileEtl, bulkFiles, "Parses")
    Rel(bulkFileEtl, postgres, "Loads tables into")
    Rel(mindsdbSetup, mindsdb, "Creates census_db in")
    Rel(mindsdb, postgres, "Queries")
```

With the major pieces named, we can watch them move. The next three sequences reuse the same `Bootstrap Path`, `Chainlit App`, `Agent Loop`, `File Export`, `MindsDB`, and `PostgreSQL` names from this container view.

## 3. The Motion

### Flow 1 - Bootstrap the bulk-file happy path

Before the `User` can reach the `Chainlit App`, `Bootstrap Path` has to make the data and `MindsDB` connection real. `scripts/init_db.sql` has already created the foundation schema when `PostgreSQL` starts, and this sequence shows the bulk-file happy path that runs when `geographies` is empty and local ACS files are present.

```mermaid
sequenceDiagram
    participant Bootstrap Path
    participant PostgreSQL
    participant Bulk File ETL
    participant MindsDB Setup
    participant MindsDB
    participant Chainlit App

    Bootstrap Path->>PostgreSQL: SELECT count(*) FROM geographies
    alt geographies already has rows
        PostgreSQL-->>Bootstrap Path: count > 0
    else geographies is empty
        PostgreSQL-->>Bootstrap Path: count = 0
        Bootstrap Path->>Bulk File ETL: run load_from_files.py
        Bulk File ETL->>PostgreSQL: load acs_catalog, geographies, and ACS Table Group tables
    end
    Bootstrap Path->>MindsDB Setup: run setup_mindsdb.py
    MindsDB Setup->>MindsDB: create census_db connection
    MindsDB->>PostgreSQL: open census_db PostgreSQL connection
    Bootstrap Path->>Chainlit App: chainlit run src/app.py
```

### Flow 2 - Answer an analytical question

Now the `User` from the context diagram reaches the running `Chainlit App`. The `Agent Loop` asks `OpenAI API` what tool to use, discovers schema through `acs_catalog`, then reads the data rows through `MindsDB` and `PostgreSQL`.

```mermaid
sequenceDiagram
    participant User
    participant Browser
    participant Chainlit App
    participant Agent Loop
    participant OpenAI API
    participant MindsDB
    participant PostgreSQL

    User->>Browser: Ask a Census question
    Browser->>Chainlit App: Send message
    Chainlit App->>Agent Loop: query_agent_stream(question, history)
    Agent Loop->>OpenAI API: completion request with tools
    OpenAI API-->>Agent Loop: search_catalog(query)
    Agent Loop->>MindsDB: query census_db.acs_catalog
    MindsDB->>PostgreSQL: SELECT from acs_catalog
    PostgreSQL-->>MindsDB: matching table_id and variable_id rows
    MindsDB-->>Agent Loop: catalog CSV
    Agent Loop->>OpenAI API: append catalog tool result
    OpenAI API-->>Agent Loop: sql_query(SELECT ...)
    Agent Loop->>MindsDB: query geographies and ACS Table Group tables
    MindsDB->>PostgreSQL: execute analytical SQL
    PostgreSQL-->>MindsDB: result rows
    MindsDB-->>Agent Loop: result CSV
    Agent Loop->>OpenAI API: append SQL tool result
    OpenAI API-->>Agent Loop: final answer
    Agent Loop-->>Chainlit App: answer and step events
    Chainlit App-->>Browser: Render final response
    Browser-->>User: Show answer
```

### Flow 3 - Produce a CSV export

This time the same `Chainlit App` and `Agent Loop` from Flow 2 stay in play, but the last leg changes. Instead of ending with a text-only answer, `Agent Loop` writes to `File Export` so the `Browser` can deliver a download.

```mermaid
sequenceDiagram
    participant User
    participant Browser
    participant Chainlit App
    participant Agent Loop
    participant OpenAI API
    participant MindsDB
    participant PostgreSQL
    participant File Export

    User->>Browser: Ask for CSV or all rows
    Browser->>Chainlit App: Send message
    Chainlit App->>Agent Loop: query_agent_stream(question, history)
    Agent Loop->>OpenAI API: completion request with tools
    OpenAI API-->>Agent Loop: search_catalog(query)
    Agent Loop->>MindsDB: query census_db.acs_catalog
    MindsDB->>PostgreSQL: SELECT from acs_catalog
    PostgreSQL-->>MindsDB: matching table_id and variable_id rows
    MindsDB-->>Agent Loop: catalog CSV
    Agent Loop->>OpenAI API: append catalog tool result
    OpenAI API-->>Agent Loop: export_csv(SELECT ..., filename)
    Agent Loop->>MindsDB: run export query through census_db
    MindsDB->>PostgreSQL: execute export SQL
    PostgreSQL-->>MindsDB: large result set
    MindsDB-->>Agent Loop: export rows
    Agent Loop->>File Export: write temp CSV and metadata
    Agent Loop-->>Chainlit App: answer and export metadata
    Chainlit App-->>Browser: Render download
    Browser-->>User: Deliver CSV file
```

The sequences tell us what happens; the next diagrams show what those movements do to long-lived things. `Chainlit App` owns the chat-facing lifecycle, while `Agent Loop` owns the tool-execution lifecycle created by Flows 2 and 3.

## 4. The State

### Chat Session lifecycle

The `Chainlit App` from the shape diagram keeps the conversation alive between requests. Its state changes are driven directly by the message and answer handoffs in Flow 2 and Flow 3 above.

```mermaid
stateDiagram-v2
    [*] --> AwaitingMessage
    AwaitingMessage --> RunningQuery: Browser sends a message in Flow 2 or Flow 3
    RunningQuery --> ShowingSteps: Chainlit App receives tool step events from Agent Loop
    RunningQuery --> PersistingHistory: Agent Loop returns a final answer without step output
    ShowingSteps --> PersistingHistory: Agent Loop returns the final answer
    PersistingHistory --> AwaitingMessage: Chainlit App appends {question, answer} to history
```

### Query Run lifecycle

The `Agent Loop` from the shape diagram creates one `Query Run` per user question. When `OpenAI API` sends `search_catalog`, `sql_query`, or `export_csv` in Flow 2 or Flow 3 above, the run moves through bounded tool states instead of improvising outside the three-tool surface.

```mermaid
stateDiagram-v2
    [*] --> AwaitingToolChoice
    AwaitingToolChoice --> RunningSearchCatalog: OpenAI API returns search_catalog in Flow 2 or Flow 3
    RunningSearchCatalog --> AwaitingToolChoice: Agent Loop appends catalog CSV
    AwaitingToolChoice --> RunningSqlQuery: OpenAI API returns sql_query in Flow 2
    AwaitingToolChoice --> RunningExportCsv: OpenAI API returns export_csv in Flow 3
    RunningSqlQuery --> AwaitingToolChoice: sql_query returns rows
    RunningExportCsv --> AwaitingToolChoice: Agent Loop appends export metadata
    RunningSqlQuery --> Retrying: sql_query returns error or 0 rows
    RunningExportCsv --> Retrying: export_csv returns error or 0 rows
    Retrying --> AwaitingToolChoice: Agent Loop raises temperature to 0.3 and retries
    Retrying --> Failed: third consecutive sql_query/export_csv failure
    AwaitingToolChoice --> Completed: OpenAI API returns final answer
```

Those lifecycle transitions only matter because the same tables keep appearing underneath them. The next view maps the rows touched in Flow 1, Flow 2, and Flow 3 to the data structures the runtime actually depends on.

## 5. The Data

`geographies` and `acs_catalog` already appeared by name in Flow 1 and Flow 2, and the analytical tables appeared there as `ACS Table Group`. This ER view ties that diagram-safe name back to the runtime `acs_<table_id>` naming pattern.

```mermaid
erDiagram
    geographies ||--o{ acs_table_family : joined_to_ACS_Table_Group
    acs_catalog ||--o{ acs_table_family : looked_up_before_ACS_Table_Group

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
        varchar variable_id PK
        varchar table_id
        text table_title
        text label
        text universe
        boolean is_estimate
    }

    acs_table_family {
        varchar geo_id PK
        integer data_year PK
        varchar table_id
        numeric variable_columns
    }
```

The `acs_table_family` node is conceptual shorthand for the runtime `acs_<table_id>` tables seen earlier as `ACS Table Group`. The ER lines describe the runtime join and lookup relationships the `Agent Loop` uses, not declared foreign keys on every auto-created ACS table.

## 6. The Risk

Finally, the happy paths above need edges. When Flow 1, Flow 2, or Flow 3 breaks, the runtime follows explicit branches instead of wandering off-model.

```mermaid
flowchart TD
    subgraph StartupRisk[Flow 1 bootstrap branches]
        S1[Flow 1 starts at Bootstrap Path] --> S2{Does geographies already have rows?}
        S2 -- yes --> S6[Skip Bulk File ETL]
        S2 -- no --> S3{Are Local ACS Bulk Files present?}
        S3 -- yes --> S4[Run Bulk File ETL]
        S3 -- no --> S5{Is CENSUS_API_KEY present?}
        S5 -- yes --> S7[Run testing fallback loader outside the primary story]
        S5 -- no --> S8[Stop startup with data-source error]
        S4 --> S9[Run MindsDB Setup]
        S6 --> S9
        S7 --> S9
        S9 --> S10[Start Chainlit App]
    end

    subgraph QueryRisk[Flow 2 and Flow 3 runtime branches]
        Q1[Flow 2 or Flow 3 reaches Agent Loop] --> Q2{What did the last tool return?}
        Q2 -- search_catalog returned weak or no matches --> Q3[OpenAI API decides whether to re-search with shorter Census terms]
        Q2 -- sql_query/export_csv returned rows --> Q9[Return answer or File Export to Chainlit App]
        Q2 -- sql_query/export_csv returned error or 0 rows --> Q4{Consecutive sql_query/export_csv failures below 3?}
        Q3 --> Q6[Ask OpenAI API for another tool call]
        Q4 -- yes --> Q5[Raise Agent Loop temperature to 0.3]
        Q5 --> Q6
        Q6 --> Q1
        Q4 -- no --> Q7[Return bounded failure message]
        Q7 --> Q8[Chainlit App shows the failure to Browser]
    end
```

That closes the zoom: the `User` from the first diagram reaches `Browser`, `Chainlit App`, and `Agent Loop`; those flows move `Query Run` and `Chat Session`; those states operate over `geographies`, `acs_catalog`, and `ACS Table Group` storage; and when the path breaks, the recovery tree stays bounded to the same named components.
