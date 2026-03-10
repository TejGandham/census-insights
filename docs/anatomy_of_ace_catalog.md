# Anatomy of `acs_catalog` — The Census Data Discovery Layer
read variables_to_catalog.md first.

`acs_catalog` is the metadata registry that bridges natural language and SQL column names. The LLM agent cannot query data tables by guessing — there are 1,193 table groups with 28,261 estimate variables. The catalog is how the agent discovers what exists and what it means.

## 1. Schema

6 columns. `variable_id` is the primary key.

```
acs_catalog
├── table_id     VARCHAR(20)  NOT NULL    -- 'B01001'
├── variable_id  VARCHAR(30)  PRIMARY KEY -- 'B01001_001E'
├── label        TEXT         NOT NULL    -- 'Estimate!!Total:'
├── table_title  TEXT                     -- 'Sex by Age'
├── universe     TEXT                     -- '' (unpopulated)
└── is_estimate  BOOLEAN      DEFAULT TRUE
```

Three indexes accelerate discovery:

```
idx_catalog_table      btree(table_id)           — fast group lookup
idx_catalog_title_fts  GIN(tsvector(table_title)) — full-text on titles
idx_catalog_label_fts  GIN(tsvector(label))       — full-text on labels
```

```mermaid
erDiagram
    acs_catalog {
        VARCHAR_20 table_id "NOT NULL — e.g. B01001"
        VARCHAR_30 variable_id "PK — e.g. B01001_001E"
        TEXT label "NOT NULL — e.g. Estimate!!Total:"
        TEXT table_title "e.g. Sex by Age"
        TEXT universe "always empty string"
        BOOLEAN is_estimate "TRUE=estimate FALSE=MOE"
    }

    geographies {
        VARCHAR_60 geo_id "PK — e.g. 0400000US06"
        VARCHAR_300 name "e.g. California"
        VARCHAR_3 sumlevel "040 050 140"
    }

    acs_b01001 {
        VARCHAR_60 geo_id "FK to geographies"
        INTEGER data_year "2019-2023"
        NUMERIC b01001_001e "estimate columns"
        NUMERIC b01001_002e "..."
    }

    acs_catalog ||--o{ acs_b01001 : "variable_id becomes column name"
    geographies ||--o{ acs_b01001 : "geo_id FK"
```

## 2. Column Deep-Dive

### `table_id` — The Organizational Unit

Each table groups related variables about a single Census topic. Format: `{prefix}{sequence}` with optional race suffix.

```mermaid
flowchart TD
    subgraph TableID["table_id Format"]
        direction TB
        BASE["Base table: B01001<br/>Sex by Age — 49 estimate variables"]
        RACE_A["B01001A — White Alone"]
        RACE_B["B01001B — Black or African American Alone"]
        RACE_C["B01001C — American Indian and Alaska Native Alone"]
        RACE_D["B01001D — Asian Alone"]
        RACE_E["B01001E — Native Hawaiian and Other Pacific Islander Alone"]
        RACE_F["B01001F — Some Other Race Alone"]
        RACE_G["B01001G — Two or More Races"]
        RACE_H["B01001H — White Alone, Not Hispanic or Latino"]
        RACE_I["B01001I — Hispanic or Latino"]
        BASE --> RACE_A
        BASE --> RACE_B
        BASE --> RACE_C
        BASE --> RACE_D
        BASE --> RACE_E
        BASE --> RACE_F
        BASE --> RACE_G
        BASE --> RACE_H
        BASE --> RACE_I
    end

    style BASE fill:#e0ffe0,stroke:#44aa44,color:#333
    style RACE_A fill:#f0f0ff,stroke:#6666cc,color:#333
    style RACE_B fill:#f0f0ff,stroke:#6666cc,color:#333
    style RACE_C fill:#f0f0ff,stroke:#6666cc,color:#333
    style RACE_D fill:#f0f0ff,stroke:#6666cc,color:#333
    style RACE_E fill:#f0f0ff,stroke:#6666cc,color:#333
    style RACE_F fill:#f0f0ff,stroke:#6666cc,color:#333
    style RACE_G fill:#f0f0ff,stroke:#6666cc,color:#333
    style RACE_H fill:#f0f0ff,stroke:#6666cc,color:#333
    style RACE_I fill:#f0f0ff,stroke:#6666cc,color:#333
```

**Real table type distribution** (2023 ACS 5-Year):

| Type | Count | Example |
|---|---|---|
| B (Detailed) | 661 | `B01001` Sex by Age |
| Race iterations | 498 | `B01001A` through `B01001I` |
| C (Collapsed) | 34 | `C17002` Ratio of Income to Poverty Level |
| **Total** | **1,193** | |

**Table size range**: 1 variable (`B01003` Total Population) to 566 variables (`B24121` Detailed Occupation by Earnings). Median is 10 variables.

### `variable_id` — The Column Key

Format: `{table_id}_{sequence}{suffix}`

```mermaid
flowchart LR
    subgraph Anatomy["variable_id: B01001_003E"]
        direction LR
        T["B01001<br/>table_id"]
        SEP["_<br/>separator"]
        NUM["003<br/>sequence<br/>(zero-padded)"]
        SUF["E<br/>estimate<br/>(or M for MOE)"]
        T --- SEP --- NUM --- SUF
    end

    subgraph DataTable["Becomes column in acs_b01001"]
        COL["b01001_003e<br/>(lowercased)"]
    end

    Anatomy -->|"ETL lowers case"| DataTable

    style T fill:#e0ffe0,stroke:#44aa44,color:#333
    style NUM fill:#fff0f0,stroke:#cc4444,color:#333
    style SUF fill:#f0f0ff,stroke:#6666cc,color:#333
    style COL fill:#fffff0,stroke:#cc9900,color:#333
```

**Real examples from B01001** (Sex by Age):

| variable_id | Suffix | Meaning |
|---|---|---|
| `B01001_001E` | E | Total population (estimate) |
| `B01001_002E` | E | Male (subtotal) |
| `B01001_003E` | E | Male, under 5 years |
| `B01001_004E` | E | Male, 5 to 9 years |
| `B01001_005E` | E | Male, 10 to 14 years |
| ... | | ... |
| `B01001_049E` | E | Female, 85 years and over |

MOE variables like `B01001_001M` exist in the Census API (referenced in the `attributes` field of each estimate) but are **not top-level entries** in `variables.json`. The ETL's `endswith("M")` filter finds zero matches. In practice, **the catalog contains only estimate rows** (28,261 of them).

### `label` — The Semantic Heart

Labels use `!!` as hierarchical separators and `:` as subtotal/terminal markers. This is the most information-dense column.

```mermaid
flowchart TD
    subgraph LabelTree["B01001 Label Hierarchy"]
        direction TB
        ROOT["Estimate!!Total:<br/>(B01001_001E)"]
        MALE["Estimate!!Total:!!Male:<br/>(B01001_002E)"]
        FEM["Estimate!!Total:!!Female:<br/>(B01001_026E)"]
        M_U5["...!!Male:!!Under 5 years<br/>(B01001_003E)"]
        M_59["...!!Male:!!5 to 9 years<br/>(B01001_004E)"]
        M_1014["...!!Male:!!10 to 14 years<br/>(B01001_005E)"]
        M_MORE["... 20 more male age brackets ..."]
        F_U5["...!!Female:!!Under 5 years<br/>(B01001_027E)"]
        F_59["...!!Female:!!5 to 9 years<br/>(B01001_028E)"]
        F_MORE["... 20 more female age brackets ..."]

        ROOT --> MALE
        ROOT --> FEM
        MALE --> M_U5
        MALE --> M_59
        MALE --> M_1014
        MALE --> M_MORE
        FEM --> F_U5
        FEM --> F_59
        FEM --> F_MORE
    end

    style ROOT fill:#e0ffe0,stroke:#44aa44,color:#333
    style MALE fill:#f0f0ff,stroke:#6666cc,color:#333
    style FEM fill:#fff0f0,stroke:#cc4444,color:#333
    style M_U5 fill:#f0f0ff,stroke:#9999dd,color:#333
    style M_59 fill:#f0f0ff,stroke:#9999dd,color:#333
    style M_1014 fill:#f0f0ff,stroke:#9999dd,color:#333
    style F_U5 fill:#fff0f0,stroke:#dd9999,color:#333
    style F_59 fill:#fff0f0,stroke:#dd9999,color:#333
```

**Hierarchy depth varies by table** — real examples from B08301 (Transportation to Work):

| variable_id | Depth | Label |
|---|---|---|
| `B08301_001E` | 1 | `Estimate!!Total:` |
| `B08301_002E` | 2 | `Estimate!!Total:!!Car, truck, or van:` |
| `B08301_003E` | 3 | `Estimate!!Total:!!Car, truck, or van:!!Drove alone` |
| `B08301_004E` | 3 | `Estimate!!Total:!!Car, truck, or van:!!Carpooled:` |
| `B08301_005E` | 4 | `Estimate!!Total:!!Car, truck, or van:!!Carpooled:!!In 2-person carpool` |
| `B08301_010E` | 2 | `Estimate!!Total:!!Public transportation (excluding taxicab):` |
| `B08301_011E` | 3 | `Estimate!!Total:!!Public transportation (excluding taxicab):!!Bus` |
| `B08301_012E` | 3 | `Estimate!!Total:!!Public transportation (excluding taxicab):!!Subway or elevated rail` |

**The `!!` trap**: Searching for `"male"` in `"Estimate!!Total:!!Male:!!Under 5 years"` fails with naive substring matching because `Male` is embedded inside `!!Male:!!`. The label-relevance scoring in `_search_catalog()` preprocesses labels — replacing `!!` and `:` with spaces — before term matching.

### `table_title` — Denormalized Group Name

Same value repeated for every variable in the same `table_id`. The `concept` field from `variables.json` is stored here.

**Real examples**:

| table_id | table_title |
|---|---|
| `B01001` | Sex by Age |
| `B01003` | Total Population |
| `B02001` | Race |
| `B08301` | Means of Transportation to Work |
| `B19013` | Median Household Income in the Past 12 Months (in 2023 Inflation-Adjusted Dollars) |
| `B25001` | Housing Units |
| `C17002` | Ratio of Income to Poverty Level in the Past 12 Months |

### `universe` — Unpopulated

Intended to hold the population being counted (e.g., "Total population", "Households", "Housing units", "Workers 16 years and over"). The `variables.json` endpoint does not provide universe per-variable — it is a table-level property from the separate `groups.json` endpoint. The ETL inserts empty string for every row.

### `is_estimate` — Estimate vs. MOE Flag

Set to `TRUE` for variables ending in `E`, `FALSE` for `M`. In practice, only `TRUE` rows exist because MOE variables are not top-level entries in the 2023 `variables.json`.

All agent queries filter `WHERE is_estimate = TRUE`.

## 3. Data Source and ETL Pipeline

The catalog is populated from a single Census Bureau endpoint — no API key required.

```mermaid
flowchart TD
    subgraph Source["Census Bureau API"]
        VARS["variables.json<br/>~30 MB, ~40,000 entries<br/>https://api.census.gov/data/2023/acs/acs5/variables.json"]
    end

    subgraph Filter["ETL Filtering (load_from_files.py or load_all_acs.py)"]
        direction TB
        F1["Skip geography fields<br/>GEO_ID, NAME, STATE, COUNTY,<br/>SUMLEVEL, TRACT, BLKGRP, etc."]
        F2["Skip annotation suffixes<br/>EA, MA, PEA, PMA"]
        F3["Skip no-group variables<br/>metadata fields without a group"]
        F4["Skip non-B/C prefixes<br/>Subject tables S*, Data Profiles DP*,<br/>Comparison Profiles CP*"]
        F5["Keep only E and M suffixes<br/>E = estimate, M = margin of error"]
        F1 --> F2 --> F3 --> F4 --> F5
    end

    subgraph Result["acs_catalog"]
        ROWS["28,261 estimate rows<br/>across 1,193 table groups<br/>0 MOE rows (not in variables.json as top-level)"]
    end

    VARS --> F1
    F5 --> ROWS

    style VARS fill:#fffff0,stroke:#cc9900,color:#333
    style ROWS fill:#e0ffe0,stroke:#44aa44,color:#333
    style F4 fill:#fff0f0,stroke:#cc4444,color:#333
```

**Raw JSON for a single variable** (B01001_001E):

```json
{
  "label": "Estimate!!Total:",
  "concept": "Sex by Age",
  "predicateType": "int",
  "group": "B01001",
  "limit": 0,
  "attributes": "B01001_001EA,B01001_001M,B01001_001MA"
}
```

The ETL maps these JSON fields to catalog columns:

| JSON field | Catalog column | Example |
|---|---|---|
| `group` | `table_id` | `B01001` |
| *(key name)* | `variable_id` | `B01001_001E` |
| `label` | `label` | `Estimate!!Total:` |
| `concept` | `table_title` | `Sex by Age` |
| *(not available)* | `universe` | `""` |
| *(derived from suffix)* | `is_estimate` | `TRUE` |

Both ETL loaders (`load_from_files.py` line 125 and `load_all_acs.py` line 175) run identical catalog population logic. The catalog is loader-independent.

## 4. How the Agent Uses the Catalog

Two-phase discovery: find tables by title, then find variables by label.

```mermaid
sequenceDiagram
    participant User
    participant Agent as LLM Agent
    participant SC as _search_catalog
    participant SCT as _search_catalog_tables
    participant MDB as MindsDB
    participant PG as PostgreSQL

    User->>Agent: "male population by age in Texas"
    Agent->>SC: search_catalog("male population age")

    Note over SC: Tokenize, lowercase, remove stop words
    Note over SC: terms = ["male", "population", "age"]

    rect rgb(230, 230, 245)
        Note over SC,PG: Phase 1 — Table Discovery
        SC->>SCT: AND mode: title LIKE male AND population AND age
        SCT->>MDB: SELECT DISTINCT table_id, table_title FROM acs_catalog
        MDB->>PG: Forward query
        PG-->>SCT: Matching tables
        Note over SCT: Score by density: hits / title words
        SCT-->>SC: Ranked tables

        SC->>SCT: OR mode: title LIKE male OR population OR age
        SCT-->>SC: Additional tables
        Note over SC: Merge AND + OR results, deduplicate, keep top 15
    end

    rect rgb(230, 245, 230)
        Note over SC,PG: Phase 2 — Variable Fetch + Label Scoring
        SC->>MDB: SELECT table_id, table_title, variable_id, label<br/>WHERE table_id IN (top 15) AND is_estimate = TRUE
        MDB->>PG: Forward query
        PG-->>SC: ~300-750 rows (all variables for 15 tables)

        Note over SC: Score each label against query terms
        Note over SC: Preprocess: replace !! and : with spaces
        Note over SC: Sort: table_rank ASC, label_score DESC, variable_id ASC
        Note over SC: Keep top 5 per table, cap at 75 total
    end

    SC-->>Agent: CSV: table_id, table_title, variable_id, label
    Note over Agent: Now knows which columns to query
    Agent->>MDB: SELECT b01001_002e, b01001_026e<br/>FROM census_db.acs_b01001<br/>JOIN census_db.geographies...
    MDB->>PG: Forward query
    PG-->>Agent: Data rows
    Agent-->>User: "Male population in Texas: 14.7M..."
```

## 5. Catalog to Data Table Mapping

Each catalog `variable_id` becomes a physical column in a PostgreSQL data table.

```mermaid
flowchart LR
    subgraph Catalog["acs_catalog rows"]
        direction TB
        V1["table_id: B01001<br/>variable_id: B01001_001E<br/>label: Estimate!!Total:"]
        V2["table_id: B01001<br/>variable_id: B01001_002E<br/>label: Estimate!!Total:!!Male:"]
        V3["table_id: B01001<br/>variable_id: B01001_003E<br/>label: ...!!Male:!!Under 5 years"]
    end

    subgraph DataTable["acs_b01001 (PostgreSQL table)"]
        direction TB
        COLS["geo_id | data_year | b01001_001e | b01001_002e | b01001_003e | ..."]
        ROW1["0400000US06 | 2023 | 39029342 | 19200970 | 1234567 | ..."]
        ROW2["0400000US48 | 2023 | 30503301 | 15200000 | 1100000 | ..."]
    end

    subgraph FileFormat[".dat file column mapping"]
        direction TB
        FC["Catalog: B01001_001E<br/>File:    B01001_E001<br/>(suffix moves before number)"]
    end

    V1 -->|"lowercase"| COLS
    V2 -->|"lowercase"| COLS
    V3 -->|"lowercase"| COLS
    V1 -.->|"ETL remaps"| FC

    style Catalog fill:#f0f0ff,stroke:#6666cc,color:#333
    style DataTable fill:#e0ffe0,stroke:#44aa44,color:#333
    style FileFormat fill:#fffff0,stroke:#cc9900,color:#333
```

**The column name transformation**:
- Catalog stores: `B01001_001E` (human convention: table_sequenceSuffix)
- Data table uses: `b01001_001e` (lowercased)
- Raw .dat files use: `B01001_E001` (suffix-first: table_SuffixSequence)

The ETL in `load_from_files.py` handles this remapping at line 438 (`_catalog_to_file_col`).

## 6. Scale Summary

All numbers verified against the 2023 ACS 5-Year `variables.json` endpoint.

| Metric | Value |
|---|---|
| Total catalog rows | 28,261 |
| Distinct table groups | 1,193 |
| B (Detailed) tables | 661 |
| Race-iteration tables | 498 |
| C (Collapsed) tables | 34 |
| Smallest table | 1 variable (B01003 Total Population) |
| Largest table | 566 variables (B24121 Occupation by Earnings) |
| Median table size | 10 variables |
| Mean table size | 23.7 variables |
| MOE rows | 0 (not top-level in variables.json) |
| Label hierarchy max depth | 4 levels (e.g. B08301 Transportation) |
