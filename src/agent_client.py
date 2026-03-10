"""Direct OpenAI agent with tool calling via MindsDB.

The agent discovers ACS data tables via a catalog search tool, then
queries specific tables through MindsDB's PostgreSQL connection.
"""

import json
import os
import re
import tempfile

import mindsdb_sdk
from openai import OpenAI

MAX_HISTORY_TURNS = 50
MAX_QUERY_ROWS = 500  # safety cap for sql_query — keep LLM context manageable
MAX_EXPORT_ROWS = 100_000  # safety cap for export_csv — full datasets OK

# Common English stop words + imperative verbs that never appear in Census metadata
_SEARCH_STOP_WORDS = frozenset({
    "a", "an", "the", "by", "in", "of", "for", "and", "or", "to",
    "on", "at", "is", "are", "was", "were", "with", "from", "as",
    "that", "this", "it", "its", "be", "has", "have", "had", "do",
    "does", "did", "will", "would", "could", "should", "may", "can",
    "what", "which", "who", "how", "where", "when", "about", "into",
    "per", "each", "all", "any", "both", "between", "through",
    "show", "me", "get", "find", "give", "tell", "list", "display", "data",
})

SYSTEM_PROMPT = (
    "You are a data analyst for US Census ACS 5-Year data (2019-2023). "
    "Answer questions clearly and concisely.\n\n"
    "## Database\n"
    "The database has ~1,193 ACS 5-Year data tables covering demographics, economics, "
    "housing, education, health insurance, commuting, language, ancestry, and more. "
    "Tables are accessed through MindsDB as `census_db.acs_<table_id>` where table_id "
    "is lowercase (e.g., `census_db.acs_b01001`).\n\n"
    "## Workflow\n"
    "1. ALWAYS start by calling `search_catalog` to find the right table and column names. "
    "Never guess table or column names — the catalog is the source of truth.\n"
    "2. Use the catalog results to identify the table_id and variable_id columns you need. "
    "Search with short Census topic keywords (e.g., 'race', 'median household income'), not full sentences.\n"
    "3. Call `sql_query` to query the data. Column names are lowercase variable IDs "
    "(e.g., `b01001_001e`). Every ACS table also has `geo_id` and `data_year` columns.\n\n"
    "## Geographies\n"
    "Join with geographies for location info:\n"
    "  `JOIN census_db.geographies g ON t.geo_id = g.geo_id`\n"
    "Columns: name, state_name, county_name, tract_code, area_type ('state', 'county', or 'tract'), "
    "state_fips, county_fips, sumlevel.\n"
    "Data is available at state, county, and census tract levels.\n\n"
    "## Rules\n"
    "- Filter by year with `data_year = 2023`. Default year: 2023 (data available 2019-2023).\n"
    "- Use `area_type = 'state'` for states, `'county'` for counties, `'tract'` for census tracts.\n"
    "- Tract GEO_IDs have format `1400000US{state_fips}{county_fips}{tract_code}`. "
    "Filter tracts by state with `g.state_fips = 'XX'`.\n"
    "- IMPORTANT: Never use IN (...) with AND. Instead use OR. "
    "Example: `(g.state_name = 'Texas' OR g.state_name = 'California')` "
    "not `g.state_name IN ('Texas', 'California')`.\n"
    "- For comparisons, use GROUP BY with AVG/SUM/COUNT.\n"
    "- Round numbers appropriately. Format currency with $ signs.\n"
    "- Present data in clear tables when appropriate.\n"
    "- When the user asks for CSV, a download, all rows, or large data exports, "
    "use the `export_csv` tool instead of `sql_query`. After calling export_csv, write "
    "a short summary (row count, columns) — do NOT paste the data into your response.\n"
    "- For analytical questions (top 10, comparisons, averages), use sql_query as normal.\n"
    "- Never execute DML (INSERT, UPDATE, DELETE, DROP)."
)

SEARCH_CATALOG_TOOL = {
    "type": "function",
    "function": {
        "name": "search_catalog",
        "description": (
            "Search the ACS data catalog to find which tables contain the data you need. "
            "Returns matching table IDs, variable names, and descriptions. "
            "ALWAYS call this first before querying data tables."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms matching ACS table titles and variable labels (e.g., 'race', 'sex by age', 'median household income', 'B19013'). Use short Census topic keywords, not full sentences.",
                }
            },
            "required": ["query"],
        },
    },
}

SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "sql_query",
        "description": (
            "Execute a read-only SQL query against the census database through MindsDB. "
            "Returns the result set as CSV text. Best for small/medium results "
            "(analytical queries, top-N, aggregations). "
            "Tables are accessed as census_db.acs_<table_id> (e.g., census_db.acs_b01001). "
            "Join with census_db.geographies for location names."
        ),
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

EXPORT_CSV_TOOL = {
    "type": "function",
    "function": {
        "name": "export_csv",
        "description": (
            "Execute a SQL query and save the FULL result set as a downloadable CSV file. "
            "Use this when the user asks for CSV, a download, all rows, raw data export, "
            "or any large result set (more than ~20 rows). Returns a confirmation with "
            "row count — the file is delivered to the user automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A SELECT SQL query to execute.",
                },
                "filename": {
                    "type": "string",
                    "description": "Name for the CSV file (e.g. 'race_by_county.csv').",
                },
            },
            "required": ["query", "filename"],
        },
    },
}

TOOLS = [SEARCH_CATALOG_TOOL, SQL_TOOL, EXPORT_CSV_TOOL]

_server = None
_client = None


def _get_server():
    global _server
    if _server is None:
        host = os.getenv("MINDSDB_HOST", "http://localhost:47334")
        _server = mindsdb_sdk.connect(host)
    return _server


def _get_openai():
    global _client
    if _client is None:
        _client = OpenAI()  # uses OPENAI_API_KEY env var
    return _client


def _check_dml(query: str) -> str | None:
    """Return error string if query is DML/DDL, else None."""
    # Strip trailing semicolons (handles whitespace like "SELECT 1 ;")
    q = re.sub(r';\s*$', '', query.strip())
    # Reject internal semicolons (multi-statement injection)
    if ';' in q:
        return "Error: Multiple SQL statements are not allowed."
    # Word-boundary scan for DML/DDL keywords (avoids false positives on column names)
    if re.search(
        r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b',
        q,
        re.IGNORECASE,
    ):
        return "Error: DML/DDL statements are not allowed."
    return None


def _search_catalog_tables(terms: list[str], *, mode: str = "and") -> list[tuple[str, str, float]]:
    """Search distinct tables by title/table_id. Returns [(table_id, title, score)] ranked by relevance."""
    conditions = []
    for term in terms:
        conditions.append(
            f"(LOWER(table_title) LIKE '%{term}%' OR LOWER(table_id) LIKE '%{term}%')"
        )

    joiner = " AND " if mode == "and" else " OR "
    where = joiner.join(conditions)

    sql = f"""
        SELECT DISTINCT table_id, table_title
        FROM census_db.acs_catalog
        WHERE is_estimate = TRUE AND ({where})
    """
    try:
        ret = _get_server().query(sql)
        df = ret.fetch()
        if df.empty:
            return []

        # Score by density: term hits / title word count.
        # B02001 "Race" → 1 hit / 1 word = 1.0
        # B11002F "...Population...Race..." → 2 hits / 10 words = 0.2
        results = []
        for _, row in df.iterrows():
            title = str(row.get("table_title", "")).lower()
            tid = str(row.get("table_id", ""))
            title_words = title.split()
            title_hits = sum(1 for t in terms if t in title)
            id_hits = sum(1 for t in terms if t in tid.lower())
            density = title_hits / max(len(title_words), 1)
            score = (density * 10) + (title_hits * 3) + (id_hits * 5)
            results.append((tid, row.get("table_title", ""), score))

        results.sort(key=lambda x: -x[2])
        return results
    except Exception as e:
        return []


def _search_catalog(query: str) -> str:
    """Search acs_catalog with two-phase table-then-variable strategy.

    Phase 1: Find matching tables (DISTINCT table_id, table_title) using AND,
    then OR fallback. Table-level search has at most ~1,193 rows so no LIMIT
    is needed and Python scoring ranks reliably.
    Phase 2: Fetch variables for the top-ranked tables.
    """
    raw_terms = [t.replace("'", "''").lower() for t in query.strip().split()]
    terms = [t for t in raw_terms if t not in _SEARCH_STOP_WORDS and len(t) > 1]

    if not terms:
        return "No matching tables found. Try shorter or different search terms."

    # Phase 1: Find tables — AND first, merge OR when multiple terms
    tables = _search_catalog_tables(terms, mode="and")
    if len(terms) > 1:
        or_tables = _search_catalog_tables(terms, mode="or")
        # Merge by score: combine both, deduplicate keeping higher score
        best = {}
        for tid, title, score in tables + or_tables:
            if tid not in best or score > best[tid][1]:
                best[tid] = (title, score)
        tables = [(tid, title, score) for tid, (title, score) in best.items()]
        tables.sort(key=lambda x: -x[2])

    if not tables:
        return "No matching tables found. Try shorter or different search terms."

    # Take top 15 tables
    top_tables = tables[:15]
    table_ids = [t[0] for t in top_tables]

    # Phase 2: Fetch variables for top tables (15 tables x ~20 vars = ~300 max)
    id_list = " OR ".join(f"table_id = '{tid}'" for tid in table_ids)
    sql = f"""
        SELECT table_id, table_title, variable_id, label
        FROM census_db.acs_catalog
        WHERE is_estimate = TRUE AND ({id_list})
    """
    try:
        ret = _get_server().query(sql)
        df = ret.fetch()
        if df.empty:
            # Shouldn't happen since we found tables, but handle gracefully
            header = "Matching tables (search catalog again with a table_id for variables):\n"
            lines = [f"  {tid}: {title}" for tid, title, _ in top_tables]
            return header + "\n".join(lines)

        # Sort by table ranking, keep first 5 variables per table, cap at 75 total
        rank = {tid: i for i, tid in enumerate(table_ids)}
        df["_rank"] = df["table_id"].map(rank).fillna(999)
        
        # Add label-relevance scoring
        df["label"] = df["label"].fillna("")
        
        def score_label(label_str):
            # Preprocess: replace !! and : with spaces, lowercase
            label_text = label_str.replace("!!", " ").replace(":", " ").lower()
            label_words = label_text.split()
            # Count term hits in label
            label_hits = sum(1 for t in terms if t in label_text)
            # Compute density: hits / word count
            label_density = label_hits / max(len(label_words), 1)
            # Score: density weighted 10x + hits weighted 3x
            return (label_density * 10) + (label_hits * 3)
        
        df["_label_score"] = df["label"].apply(score_label)
        
        # Sort by table rank (ascending), then label score (descending), then variable_id (ascending)
        df = df.sort_values(["_rank", "_label_score", "variable_id"], ascending=[True, False, True])
        df = df.groupby("table_id", sort=False).head(5)
        df = df.drop(columns=["_rank", "_label_score"]).head(75)

        return df.to_csv(index=False)
    except Exception as e:
        return f"Error searching catalog: {e}"


def _extract_table_id(sql: str) -> str | None:
    """Extract table_id from SQL like census_db.acs_b01001 → B01001 (uppercase)."""
    m = re.search(r'census_db\.acs_(\w+)', sql, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _auto_search_catalog(table_id: str) -> str:
    """Quick catalog lookup by table_id (uppercase). Returns CSV or error."""
    safe_id = table_id.replace("'", "''")
    sql = f"""
        SELECT table_id, variable_id, label
        FROM census_db.acs_catalog
        WHERE is_estimate = TRUE AND table_id = '{safe_id}'
        ORDER BY variable_id
        LIMIT 20
    """
    try:
        ret = _get_server().query(sql)
        df = ret.fetch()
        if df.empty:
            return f"No catalog entries found for table {table_id}."
        return df.to_csv(index=False)
    except Exception:
        return f"Could not look up columns for table {table_id}."


def _classify_sql_error(error_msg: str, original_sql: str) -> str:
    """Classify a SQL error and return structured feedback with hints."""
    err_lower = str(error_msg).lower()
    table_id = _extract_table_id(original_sql)
    auto_cols = ""
    if table_id:
        auto_cols = _auto_search_catalog(table_id)

    if "column" in err_lower and ("not found" in err_lower or "does not exist" in err_lower):
        hint = "The column name may be wrong. Re-search the catalog for correct variable IDs."
        if auto_cols and "No catalog entries" not in auto_cols:
            hint += f"\n\nAvailable columns for {table_id}:\n{auto_cols}"
        return f"SQL Error: {error_msg}\nHint: {hint}\nFailed SQL: {original_sql}"

    if ("relation" in err_lower or "table" in err_lower) and ("not found" in err_lower or "does not exist" in err_lower):
        hint = "The table may not exist. Check the table_id format (lowercase in SQL, e.g., census_db.acs_b01001)."
        if auto_cols and "No catalog entries" not in auto_cols:
            hint += f"\n\nCatalog entries for {table_id}:\n{auto_cols}"
        return f"SQL Error: {error_msg}\nHint: {hint}\nFailed SQL: {original_sql}"

    if "syntax" in err_lower:
        return f"SQL Error: {error_msg}\nHint: Check SQL syntax near the reported position.\nFailed SQL: {original_sql}"

    if "ambiguous" in err_lower:
        return f"SQL Error: {error_msg}\nHint: Use table-qualified column names (e.g., t.column_name).\nFailed SQL: {original_sql}"

    return f"SQL Error: {error_msg}\nHint: Review the error message and try a different approach.\nFailed SQL: {original_sql}"


def _execute_sql(query: str) -> str:
    """Run SQL via MindsDB and return CSV string."""
    err = _check_dml(query)
    if err:
        return err

    try:
        ret = _get_server().query(query)
        df = ret.fetch()
        if len(df) == 0:
            return (
                f"Query returned 0 rows. The query executed successfully but found no matching data.\n"
                f"Hint: Check filter values (year, geography, column names). Try broadening your WHERE clause.\n"
                f"Failed SQL: {query}"
            )
        if len(df) > MAX_QUERY_ROWS:
            df = df.head(MAX_QUERY_ROWS)
            note = f"\n[Truncated to {MAX_QUERY_ROWS} rows. Use export_csv for full results.]"
        else:
            note = ""
        return df.to_csv(index=False) + note
    except Exception as e:
        return _classify_sql_error(str(e), query)


def _export_csv(query: str, filename: str) -> dict:
    """Run SQL via MindsDB, write full CSV to temp file. Returns dict with path and metadata."""
    err = _check_dml(query)
    if err:
        return {"error": err}

    try:
        ret = _get_server().query(query)
        df = ret.fetch()
        if len(df) == 0:
            return {
                "error": (
                    f"Query returned 0 rows. The query executed successfully but found no matching data.\n"
                    f"Hint: Check filter values (year, geography, column names).\n"
                    f"Failed SQL: {query}"
                )
            }
        if len(df) > MAX_EXPORT_ROWS:
            df = df.head(MAX_EXPORT_ROWS)

        # Write to temp file that persists until app cleanup
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", prefix="export_", delete=False
        )
        df.to_csv(tmp, index=False)
        tmp.close()

        return {
            "path": tmp.name,
            "filename": filename,
            "rows": len(df),
            "columns": list(df.columns),
        }
    except Exception as e:
        return {"error": _classify_sql_error(str(e), query)}


def _handle_tool_call(tc) -> tuple[str, dict | None]:
    """Handle a single tool call. Returns (message_content, export_dict_or_None)."""
    args = json.loads(tc.function.arguments)

    if tc.function.name == "search_catalog":
        result = _search_catalog(args["query"])
        return result, None

    elif tc.function.name == "sql_query":
        result = _execute_sql(args["query"])
        return result, None

    elif tc.function.name == "export_csv":
        export = _export_csv(args["query"], args.get("filename", "export.csv"))
        if "error" in export:
            err_msg = export["error"]
            # Avoid double-prefix when error already has a structured prefix
            if err_msg.startswith(("SQL Error:", "Query returned", "Error:")):
                return err_msg, None
            return f"Error: {err_msg}", None
        else:
            content = (
                f"CSV file saved: {export['filename']} "
                f"({export['rows']} rows, columns: {', '.join(export['columns'])}). "
                f"The file will be delivered to the user as a download."
            )
            return content, export

    else:
        return f"Error: Unknown tool {tc.function.name}", None


def _build_messages(history: list[dict] | None, question: str) -> list[dict]:
    """Build OpenAI chat messages from history + current question."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        for turn in history[-MAX_HISTORY_TURNS:]:
            messages.append({"role": "user", "content": turn["question"]})
            messages.append({"role": "assistant", "content": turn["answer"]})
    messages.append({"role": "user", "content": question})
    return messages


def _is_error_or_empty(tool_result: str) -> bool:
    """Check if a tool result indicates an error or empty result set."""
    r = tool_result.lower()
    return r.startswith(("error:", "sql error:", "query returned 0 rows"))


def query_agent(question: str, history: list[dict] | None = None) -> tuple[str, list]:
    """Run the agent loop: LLM decides whether to call search_catalog, sql_query, or export_csv."""
    client = _get_openai()
    model = os.getenv("LLM_MODEL", "gpt-4.1")
    messages = _build_messages(history, question)
    exports = []  # collect export_csv results for app.py
    temperature = 0.0
    consecutive_errors = 0
    last_error_content = ""

    for _ in range(10):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            temperature=temperature,
        )
        choice = response.choices[0]

        if choice.finish_reason == "stop":
            return choice.message.content or "", exports

        if choice.finish_reason == "tool_calls":
            messages.append(choice.message)
            had_error = False
            for tc in choice.message.tool_calls:
                content, export = _handle_tool_call(tc)
                if export:
                    exports.append(export)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
                })
                if tc.function.name in ("sql_query", "export_csv") and _is_error_or_empty(content):
                    had_error = True
                    last_error_content = content

            if had_error:
                temperature = 0.3
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    msg = f"I wasn't able to get a working query after several attempts. Last error:\n{last_error_content}"
                    return msg, exports
            else:
                temperature = 0.0
                consecutive_errors = 0
        else:
            return choice.message.content or "", exports

    return "I was unable to complete the request within the allowed number of steps.", exports


def query_agent_stream(question: str, history: list[dict] | None = None):
    """Streaming version — yields dicts with 'steps', 'output', and 'exports' keys."""
    client = _get_openai()
    model = os.getenv("LLM_MODEL", "gpt-4.1")
    messages = _build_messages(history, question)
    temperature = 0.0
    consecutive_errors = 0
    last_error_content = ""

    for _ in range(10):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            temperature=temperature,
        )
        choice = response.choices[0]

        if choice.finish_reason == "stop":
            yield {"output": choice.message.content or ""}
            return

        if choice.finish_reason == "tool_calls":
            messages.append(choice.message)
            had_error = False
            for tc in choice.message.tool_calls:
                args = json.loads(tc.function.arguments)
                content, export = _handle_tool_call(tc)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
                })

                if tc.function.name in ("sql_query", "export_csv") and _is_error_or_empty(content):
                    had_error = True
                    last_error_content = content

                # Determine tool name and input for the step display
                tool_name = tc.function.name
                if tool_name == "search_catalog":
                    tool_input = args["query"]
                elif tool_name == "sql_query":
                    tool_input = args["query"]
                elif tool_name == "export_csv":
                    tool_input = args["query"]
                else:
                    tool_input = str(args)

                step = {
                    "steps": [{
                        "action": {"tool": tool_name, "tool_input": tool_input},
                        "observation": content[:2000] + ("..." if len(content) > 2000 else ""),
                    }]
                }
                if export:
                    step["exports"] = [export]
                yield step

            if had_error:
                temperature = 0.3
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    yield {"output": f"I wasn't able to get a working query after several attempts. Last error:\n{last_error_content}"}
                    return
            else:
                temperature = 0.0
                consecutive_errors = 0

        else:
            yield {"output": choice.message.content or ""}
            return

    yield {"output": "I was unable to complete the request within the allowed number of steps."}
