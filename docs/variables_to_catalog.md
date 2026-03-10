# ETL Code Path: How `variables.json` Becomes `acs_catalog`

This document traces the exact code path that populates the `acs_catalog` table. Both loaders (`load_from_files.py` line 125 and `load_all_acs.py` line 175) run identical logic — the catalog is loader-independent.

## 1. Entry: Idempotency Guard

```python
def load_catalog(conn):
    if table_has_rows(conn, "acs_catalog"):
        log.info("acs_catalog already populated — skipping.")
        return
```

Checks if the table already has data. If yes, exits immediately. This makes the ETL re-runnable without duplicating rows.

## 2. Fetch: Download variables.json

```python
resp = requests.get(VARIABLES_JSON_URL, timeout=120)
data = resp.json()
variables = data.get("variables", {})
```

Downloads `https://api.census.gov/data/2023/acs/acs5/variables.json` — ~30 MB, no API key needed. The response is a single JSON object with one key:

```json
{ "variables": { "GEO_ID": {...}, "NAME": {...}, "B01001_001E": {...}, ... } }
```

**28,299 raw entries** land in `variables`. These are everything the Census API publishes — geography fields, annotations, metadata, plus the actual data variables.

## 3. The Filter Pipeline

The loop iterates all 28,299 entries and applies 5 sequential filters. Each entry is either skipped or kept.

```mermaid
flowchart TD
    subgraph Pipeline["Filter Pipeline — Real Counts"]
        direction TB
        RAW["28,299 raw entries<br/>from variables.json"]
        F1["Filter 1: SKIP_VARIABLE_NAMES<br/>Removes: GEO_ID, NAME, STATE, COUNTY,<br/>SUMLEVEL, TRACT, BLKGRP, etc.<br/>11 removed"]
        F2["Filter 2: ANNOTATION_SUFFIXES<br/>Removes: names ending in EA, MA, PEA, PMA<br/>1 removed (rest caught by Filter 3)"]
        F3["Filter 3: No group<br/>Removes: entries where group is N/A or empty<br/>26 removed"]
        F4["Filter 4: Non-B/C prefix<br/>Removes: Subject tables S*, Data Profiles DP*<br/>0 removed (only B/C exist at this endpoint)"]
        F5["Filter 5: Suffix classification<br/>E suffix = estimate (28,261 kept)<br/>M suffix = MOE (0 kept — not top-level)<br/>Other = skip (0)"]
        RESULT["28,261 rows to insert"]

        RAW --> F1 --> F2 --> F3 --> F4 --> F5 --> RESULT
    end

    style RAW fill:#fffff0,stroke:#cc9900,color:#333
    style F1 fill:#fff0f0,stroke:#cc4444,color:#333
    style F2 fill:#fff0f0,stroke:#cc4444,color:#333
    style F3 fill:#fff0f0,stroke:#cc4444,color:#333
    style F4 fill:#f0f0ff,stroke:#6666cc,color:#333
    style F5 fill:#f0f0ff,stroke:#6666cc,color:#333
    style RESULT fill:#e0ffe0,stroke:#44aa44,color:#333
```

### Filter counts verified against live Census API

| Filter | Check | Removed | Remaining |
|---|---|---|---|
| Start | — | — | 28,299 |
| 1. Skip names | `var_name in SKIP_VARIABLE_NAMES` | 11 | 28,288 |
| 2. Skip annotations | `endswith(EA/MA/PEA/PMA)` | 1 | 28,287 |
| 3. Skip no-group | `group == "N/A" or not group` | 26 | 28,261 |
| 4. Skip non-B/C | `not group.startswith(B/C)` | 0 | 28,261 |
| 5. Suffix classify | E=kept, M=kept, other=skip | 0 | **28,261** |

## 4. What Each Filter Catches — Real Examples

### Filter 1: Geography and metadata fields

The constant `SKIP_VARIABLE_NAMES` contains:

```python
{"for", "in", "ucgid", "GEO_ID", "NAME", "GEOCOMP",
 "SUMLEVEL", "STATE", "COUNTY", "PLACE", "TRACT", "BLKGRP"}
```

Example entry killed here — `GEO_ID`:

```json
{
  "label": "Geography",
  "concept": "Sex by Age;Sex by Age (White Alone);...",
  "predicateType": "string",
  "group": "B18104,B17015,...",
  "attributes": "NAME"
}
```

This is a geography predicate, not a data variable. `"GEO_ID"` matches the skip set — removed.

### Filter 2: Annotation suffixes

The constant `ANNOTATION_SUFFIXES` contains `("EA", "MA", "PEA", "PMA")`.

Example entry killed here — `B01001_001EA`:

```json
{}
```

Empty metadata. The `EA` suffix marks it as an annotation for `B01001_001E`. Most annotation variables have empty `{}` meta and would also be caught by Filter 3, but this filter catches them first by name pattern.

### Filter 3: No-group variables

Example entry killed here — `NAME`:

```json
{}
```

Empty metadata, no `group` field. These are structural API fields with no data content.

### Filter 4: Non-B/C prefix (0 hits in practice)

Would catch Subject tables (`S0101_C01_001E`), Data Profiles (`DP02_0001E`), Comparison Profiles (`CP02_0001E`). In the 2023 ACS 5-Year `variables.json` endpoint, only B and C variables appear as top-level entries — so this filter removes 0.

The constant `VALID_TABLE_PREFIXES` contains `("B", "C")`.

### Filter 5: Suffix classification

```python
if var_name.endswith("E"):
    is_estimate = True      # 28,261 matches
elif var_name.endswith("M"):
    is_estimate = False     # 0 matches (MOE not top-level)
else:
    continue                # 0 caught
```

MOE variables like `B01001_001M` are referenced in the `attributes` field of estimate variables but do not exist as top-level keys in `variables.json`. The `endswith("M")` branch correctly yields 0 matches.

## 5. A Real Entry Through the Full Pipeline

`B01001_001E` — raw JSON from Census API:

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

```mermaid
flowchart LR
    subgraph Input["Raw JSON Entry"]
        KEY["Key: B01001_001E"]
        META["label: Estimate!!Total:<br/>concept: Sex by Age<br/>group: B01001<br/>predicateType: int<br/>attributes: ...EA,...M,...MA"]
    end

    subgraph Filters["5 Filters"]
        direction TB
        C1["1. B01001_001E not in<br/>SKIP_VARIABLE_NAMES<br/>PASS"]
        C2["2. Does not end in<br/>EA, MA, PEA, PMA<br/>PASS"]
        C3["3. group = B01001<br/>not N/A or empty<br/>PASS"]
        C4["4. B01001 starts with B<br/>valid prefix<br/>PASS"]
        C5["5. Ends with E<br/>is_estimate = True<br/>KEPT"]
        C1 --> C2 --> C3 --> C4 --> C5
    end

    subgraph Output["Catalog Row Tuple"]
        ROW["( B01001,<br/>  B01001_001E,<br/>  Estimate!!Total:,<br/>  Sex by Age,<br/>  empty string,<br/>  True )"]
    end

    KEY --> C1
    C5 --> ROW

    style C1 fill:#e0ffe0,stroke:#44aa44,color:#333
    style C2 fill:#e0ffe0,stroke:#44aa44,color:#333
    style C3 fill:#e0ffe0,stroke:#44aa44,color:#333
    style C4 fill:#e0ffe0,stroke:#44aa44,color:#333
    style C5 fill:#e0ffe0,stroke:#44aa44,color:#333
    style ROW fill:#f0f0ff,stroke:#6666cc,color:#333
```

## 6. Row Construction

Each surviving variable becomes a 6-tuple mapped from the JSON fields:

```python
rows.append((
    group,        # table_id    → "B01001"
    var_name,     # variable_id → "B01001_001E"
    label,        # label       → "Estimate!!Total:"
    concept,      # table_title → "Sex by Age"
    "",           # universe    → "" (not in variables.json per-variable)
    is_estimate,  # is_estimate → True
))
```

```mermaid
flowchart LR
    subgraph JSON["variables.json fields"]
        direction TB
        J1["meta.group"]
        J2["key name"]
        J3["meta.label"]
        J4["meta.concept"]
        J5["(not available)"]
        J6["(derived from suffix)"]
    end

    subgraph Catalog["acs_catalog columns"]
        direction TB
        C1["table_id<br/>B01001"]
        C2["variable_id<br/>B01001_001E"]
        C3["label<br/>Estimate!!Total:"]
        C4["table_title<br/>Sex by Age"]
        C5["universe<br/>(empty string)"]
        C6["is_estimate<br/>True"]
    end

    J1 --> C1
    J2 --> C2
    J3 --> C3
    J4 --> C4
    J5 --> C5
    J6 --> C6

    style JSON fill:#fffff0,stroke:#cc9900,color:#333
    style Catalog fill:#e0ffe0,stroke:#44aa44,color:#333
```

The `universe` field is always empty string because `variables.json` does not provide universe per-variable — it is a table-level property from the separate `groups.json` endpoint that was never integrated.

## 7. Bulk Insert

```python
execute_values(
    cur,
    """INSERT INTO acs_catalog (table_id, variable_id, label, table_title,
           universe, is_estimate)
       VALUES %s
       ON CONFLICT (variable_id) DO NOTHING""",
    rows,
    page_size=5000,
)
conn.commit()
```

- `psycopg2.extras.execute_values` — bulk insert, 5,000 rows per batch
- `ON CONFLICT (variable_id) DO NOTHING` — idempotent, skip duplicates silently
- 28,261 rows across ~6 batches
- Single `commit()` at the end — all-or-nothing

## 8. Entries That Don't Survive — Comparison

```mermaid
flowchart TD
    subgraph Killed["Entries removed by filters"]
        direction TB
        K1["GEO_ID<br/>Filter 1: skip name<br/>Geography predicate, not data"]
        K2["NAME<br/>Filter 3: no group<br/>Empty metadata"]
        K3["B01001_001EA<br/>Filter 2: annotation suffix<br/>Empty metadata, EA suffix"]
        K4["S0101_C01_001E<br/>Filter 3: no group<br/>Subject table var, empty meta<br/>(Filter 4 would also catch it)"]
    end

    subgraph Kept["Entries that become catalog rows"]
        direction TB
        V1["B01001_001E<br/>All 5 filters passed<br/>Estimate for Sex by Age total"]
        V2["B02001_002E<br/>All 5 filters passed<br/>Estimate for Race: White alone"]
        V3["B19013_001E<br/>All 5 filters passed<br/>Estimate for Median household income"]
    end

    style Killed fill:#fff0f0,stroke:#cc4444,color:#333
    style Kept fill:#e0ffe0,stroke:#44aa44,color:#333
```

## 9. Both Loaders Produce Identical Output

```mermaid
flowchart TD
    subgraph Source["Same Source"]
        API["variables.json<br/>https://api.census.gov/data/2023/acs/acs5/variables.json"]
    end

    subgraph Loader1["load_from_files.py (line 125)"]
        L1["load_catalog(conn)<br/>Same filters, same row construction,<br/>same execute_values, same page_size"]
    end

    subgraph Loader2["load_all_acs.py (line 175)"]
        L2["load_catalog(conn)<br/>Same filters, same row construction,<br/>same execute_values, same page_size"]
    end

    subgraph Result["Same Output"]
        CAT["acs_catalog<br/>28,261 rows<br/>1,193 table groups<br/>Estimates only"]
    end

    API --> L1 --> CAT
    API --> L2 --> CAT

    style API fill:#fffff0,stroke:#cc9900,color:#333
    style L1 fill:#f0f0ff,stroke:#6666cc,color:#333
    style L2 fill:#f0f0ff,stroke:#6666cc,color:#333
    style CAT fill:#e0ffe0,stroke:#44aa44,color:#333
```

The only difference between loaders is what happens AFTER catalog population — `load_from_files.py` reads local `.dat` bulk files while `load_all_acs.py` calls the Census API per table. The catalog step is identical.
