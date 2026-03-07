"""ETL: Load ALL US Census ACS 5-Year Detailed Tables into PostgreSQL.

Replaces the old load_census_data.py which loaded only 48 hand-picked variables.
This script loads every variable from every B- and C-prefix table (~1,100+ tables)
for years 2019-2023.

Three-step process:
  1. Populate acs_catalog from the Census variables.json endpoint
  2. Populate geographies table (states + counties)
  3. For each ACS table group, CREATE TABLE + INSERT data for all years

Usage:
    python scripts/load_all_acs.py

Requires:
    CENSUS_API_KEY env var (get one at https://api.census.gov/data/key_signup.html)
    PostgreSQL running with schema from init_db.sql already applied
"""

import os
import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
from psycopg2.extras import execute_values
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY")
if not CENSUS_API_KEY:
    print("ERROR: CENSUS_API_KEY environment variable is required.")
    print("Get a free key at https://api.census.gov/data/key_signup.html")
    sys.exit(1)

PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = os.getenv("POSTGRES_PORT", "5432")
PG_USER = os.getenv("POSTGRES_USER", "census_admin")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "census_pass")
PG_DB = os.getenv("POSTGRES_DB", "census_data")

YEARS = [2019, 2020, 2021, 2022, 2023]

# Census API returns data as list-of-lists; first row is headers.
# The API allows up to 50 variables per call (NAME counts as one).
# We use 49 data vars + NAME per batch to stay safely under the limit.
VARS_PER_BATCH = 49

# Parallelism: number of concurrent API requests
MAX_WORKERS = 15

# Census API sentinel values that mean "data not available"
CENSUS_SENTINELS = {
    -666666666, -555555555, -333333333, -222222222, -999999999,
    -666666666.0, -555555555.0, -333333333.0, -222222222.0, -999999999.0,
}

# Variables / prefixes to skip when parsing variables.json
# These are geography identifiers, annotations, or metadata — not data.
SKIP_VARIABLE_NAMES = {
    "for", "in", "ucgid", "GEO_ID", "NAME", "GEOCOMP",
    "SUMLEVEL", "STATE", "COUNTY", "PLACE", "TRACT", "BLKGRP",
}

# Annotation suffixes to skip
ANNOTATION_SUFFIXES = ("EA", "MA", "PEA", "PMA")

# We only want Detailed Tables (B- and C-prefix). Skip Subject (S), Data
# Profile (DP), Comparison Profile (CP), and Supplemental Estimates (K).
VALID_TABLE_PREFIXES = ("B", "C")

BASE_URL = "https://api.census.gov/data"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_connection():
    """Return a new psycopg2 connection."""
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, dbname=PG_DB
    )


def table_has_rows(conn, table_name):
    """Check if a table exists and has at least one row."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=%s)",
                (table_name,),
            )
            if not cur.fetchone()[0]:
                return False
            cur.execute(f"SELECT 1 FROM {table_name} LIMIT 1")  # noqa: S608
            return cur.fetchone() is not None
    except Exception:
        conn.rollback()
        return False


# ---------------------------------------------------------------------------
# Census API helpers
# ---------------------------------------------------------------------------
def census_get(url, max_retries=3):
    """GET a Census API URL with exponential-backoff retry.

    Returns the parsed JSON (list-of-lists) on success, or None on failure.
    """
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 204:
                # No data available for this combination
                return None
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = 2 ** attempt + 1
                log.warning(
                    "Census API returned %s for %s — retrying in %ds (attempt %d/%d)",
                    resp.status_code, url[:120], wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
                continue
            # Other error (e.g., 400 = bad variable for that year)
            log.debug("Census API %s for %s", resp.status_code, url[:120])
            return None
        except requests.RequestException as exc:
            wait = 2 ** attempt + 1
            log.warning(
                "Request error for %s: %s — retrying in %ds", url[:120], exc, wait
            )
            time.sleep(wait)
    log.error("Failed after %d retries: %s", max_retries, url[:120])
    return None


def safe_numeric(val):
    """Convert a Census API string value to a Python numeric, or None.

    Handles sentinel values, empty strings, None, and non-numeric strings.
    """
    if val is None or val == "" or val == "null":
        return None
    try:
        f = float(val)
        if f in CENSUS_SENTINELS or f < -99_999_999:
            return None
        # Return int-compatible values as int to keep NUMERIC storage lean
        if f == int(f) and abs(f) < 1e15:
            return int(f)
        return f
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Step 1: Populate acs_catalog
# ---------------------------------------------------------------------------
def load_catalog(conn):
    """Fetch variables.json and populate the acs_catalog table."""
    if table_has_rows(conn, "acs_catalog"):
        log.info("acs_catalog already populated — skipping.")
        return

    log.info("Fetching variables.json from Census API (this is ~30 MB)...")
    url = f"{BASE_URL}/2023/acs/acs5/variables.json"
    data = census_get(url)
    if data is None:
        log.error("Failed to fetch variables.json")
        sys.exit(1)

    variables = data.get("variables", {})
    log.info("Parsing %d raw variable entries...", len(variables))

    rows = []
    for var_name, meta in variables.items():
        # Skip geography / metadata fields
        if var_name.upper() in SKIP_VARIABLE_NAMES or var_name in SKIP_VARIABLE_NAMES:
            continue

        # Skip annotations (EA, MA, PEA, PMA suffixes)
        if any(var_name.endswith(suffix) for suffix in ANNOTATION_SUFFIXES):
            continue

        # Skip variables that are not in a group (geography predicates, etc.)
        group = meta.get("group", "N/A")
        if group == "N/A" or not group:
            continue

        # Only keep Detailed Tables (B and C prefix)
        if not any(group.startswith(p) for p in VALID_TABLE_PREFIXES):
            continue

        # Determine estimate vs. margin-of-error
        # Variable names ending in E (or PE) are estimates,
        # M (or PM) are margins of error.
        # endswith("E") covers both *_001E and *_001PE, etc.
        if var_name.endswith("E"):
            is_estimate = True
        elif var_name.endswith("M"):
            is_estimate = False
        else:
            # Unexpected suffix — skip
            continue

        label = meta.get("label", "")
        concept = meta.get("concept", "")  # This is the table title
        # predicateType tells us the data type but we store everything as NUMERIC

        rows.append((
            group,       # table_id
            var_name,    # variable_id (PK)
            label,
            concept,     # -> table_title
            "",          # universe — not directly in variables.json per-variable
            is_estimate,
        ))

    log.info("Inserting %d catalog entries...", len(rows))
    with conn.cursor() as cur:
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
    log.info("acs_catalog: %d rows inserted.", len(rows))


# ---------------------------------------------------------------------------
# Step 2: Populate geographies
# ---------------------------------------------------------------------------
def load_geographies(conn):
    """Fetch state and county geographies from Census API and insert."""
    if table_has_rows(conn, "geographies"):
        log.info("geographies already populated — skipping.")
        return

    log.info("Fetching state geographies...")
    state_url = (
        f"{BASE_URL}/2023/acs/acs5?get=NAME&for=state:*&key={CENSUS_API_KEY}"
    )
    state_data = census_get(state_url)
    if state_data is None or len(state_data) < 2:
        log.error("Failed to fetch state geographies")
        sys.exit(1)

    log.info("Fetching county geographies...")
    county_url = (
        f"{BASE_URL}/2023/acs/acs5?get=NAME&for=county:*&key={CENSUS_API_KEY}"
    )
    county_data = census_get(county_url)
    if county_data is None or len(county_data) < 2:
        log.error("Failed to fetch county geographies")
        sys.exit(1)

    rows = []

    # Parse state data: [["NAME","state"], ["Alabama","01"], ...]
    state_headers = state_data[0]
    name_idx = state_headers.index("NAME")
    state_idx = state_headers.index("state")

    for row in state_data[1:]:
        state_fips = row[state_idx]
        name = row[name_idx]
        geo_id = f"0400000US{state_fips}"
        rows.append((
            geo_id,        # geo_id
            name,          # name
            "040",         # sumlevel
            state_fips,    # state_fips
            None,          # county_fips
            None,          # tract_code
            name,          # state_name
            None,          # county_name
        ))

    # Parse county data: [["NAME","state","county"], ["Autauga County, Alabama","01","001"], ...]
    county_headers = county_data[0]
    cname_idx = county_headers.index("NAME")
    cstate_idx = county_headers.index("state")
    ccounty_idx = county_headers.index("county")

    for row in county_data[1:]:
        state_fips = row[cstate_idx]
        county_fips = row[ccounty_idx]
        name = row[cname_idx]
        geo_id = f"0500000US{state_fips}{county_fips}"

        # Parse "County Name, State Name"
        parts = name.split(", ", 1)
        county_name = parts[0] if parts else name
        state_name = parts[1] if len(parts) > 1 else None

        rows.append((
            geo_id,
            name,
            "050",
            state_fips,
            county_fips,
            None,          # tract_code
            state_name,
            county_name,
        ))

    log.info("Inserting %d geographies (%d states, %d counties)...",
             len(rows), len(state_data) - 1, len(county_data) - 1)

    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO geographies
                   (geo_id, name, sumlevel, state_fips, county_fips,
                    tract_code, state_name, county_name)
               VALUES %s
               ON CONFLICT (geo_id) DO NOTHING""",
            rows,
            page_size=5000,
        )
    conn.commit()
    log.info("geographies: %d rows inserted.", len(rows))


# ---------------------------------------------------------------------------
# Step 3: Load ACS data tables
# ---------------------------------------------------------------------------
def get_table_variables(conn):
    """Query acs_catalog for estimate variables, grouped by table_id.

    Returns:
        dict: {table_id: {"title": str, "vars": [variable_id, ...]}}
    """
    tables = {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT table_id, variable_id, table_title
               FROM acs_catalog
               WHERE is_estimate = TRUE
               ORDER BY table_id, variable_id"""
        )
        for table_id, var_id, title in cur.fetchall():
            if table_id not in tables:
                tables[table_id] = {"title": title or "", "vars": []}
            tables[table_id]["vars"].append(var_id)
    return tables


def fetch_batch(year, var_list, geo_level):
    """Fetch a single batch of variables from Census API.

    Args:
        year: ACS data year
        var_list: list of variable IDs (max 49)
        geo_level: 'state' or 'county'

    Returns:
        list of dicts, one per geography row, or None on failure.
        Each dict maps variable_id -> string value, plus 'state' and
        optionally 'county' keys.
    """
    vars_str = ",".join(["NAME"] + var_list)
    if geo_level == "state":
        url = (
            f"{BASE_URL}/{year}/acs/acs5"
            f"?get={vars_str}&for=state:*&key={CENSUS_API_KEY}"
        )
    else:
        url = (
            f"{BASE_URL}/{year}/acs/acs5"
            f"?get={vars_str}&for=county:*&key={CENSUS_API_KEY}"
        )

    data = census_get(url)
    if data is None or len(data) < 2:
        return None

    headers = data[0]
    results = []
    for row in data[1:]:
        record = {}
        for i, h in enumerate(headers):
            record[h] = row[i]
        results.append(record)
    return results


def fetch_table_year(table_id, var_list, year):
    """Fetch all data for one table + year, combining states and counties.

    Handles batching (49 vars per call) and merging multiple batches.
    Uses ThreadPoolExecutor to parallelize API calls across batches and
    geo levels (state vs county).

    Returns:
        list of dicts with keys: 'geo_id', 'data_year', plus one key per
        variable_id with numeric values. Returns empty list on failure.
    """
    # Split variables into batches of VARS_PER_BATCH
    batches = [
        var_list[i : i + VARS_PER_BATCH]
        for i in range(0, len(var_list), VARS_PER_BATCH)
    ]

    # Submit all API calls in parallel: each (batch, geo_level) pair
    fetch_jobs = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for geo_level in ("state", "county"):
            for batch in batches:
                future = executor.submit(fetch_batch, year, batch, geo_level)
                fetch_jobs.append((future, batch, geo_level))

        # Collect results and merge across batches
        merged = {}  # geo_id -> {var_id: value, ...}

        for future, batch, geo_level in fetch_jobs:
            try:
                results = future.result(timeout=180)
            except Exception as exc:
                log.warning(
                    "  %s/%d batch fetch failed (%s, %d vars): %s",
                    table_id, year, geo_level, len(batch), exc,
                )
                continue

            if results is None:
                continue

            for rec in results:
                state_fips = rec.get("state", "")
                county_fips = rec.get("county")

                if geo_level == "state":
                    geo_id = f"0400000US{state_fips}"
                else:
                    if county_fips is None:
                        continue
                    geo_id = f"0500000US{state_fips}{county_fips}"

                if geo_id not in merged:
                    merged[geo_id] = {}

                for var_id in batch:
                    raw_val = rec.get(var_id)
                    merged[geo_id][var_id] = safe_numeric(raw_val)

    # Convert merged dict to list of row tuples
    rows = []
    for geo_id, var_vals in merged.items():
        row = {"geo_id": geo_id, "data_year": year}
        row.update(var_vals)
        rows.append(row)

    return rows


def create_acs_table(conn, table_id, var_list):
    """DROP + CREATE TABLE for one ACS data table.

    Table name: acs_{table_id_lowercase}
    Columns: geo_id, data_year, plus one NUMERIC column per variable.
    Column names are the variable_id lowercased.
    """
    pg_table = f"acs_{table_id.lower()}"
    col_defs = []
    for var_id in var_list:
        col_name = var_id.lower()
        col_defs.append(f"    {col_name} NUMERIC")

    col_block = ",\n".join(col_defs)

    ddl = f"""
DROP TABLE IF EXISTS {pg_table} CASCADE;
CREATE TABLE {pg_table} (
    geo_id    VARCHAR(60) NOT NULL,
    data_year INTEGER NOT NULL,
{col_block},
    PRIMARY KEY (geo_id, data_year)
);
"""
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()
    return pg_table


def insert_table_data(conn, pg_table, var_list, all_rows):
    """Bulk-insert rows into an ACS data table.

    Args:
        conn: psycopg2 connection
        pg_table: PostgreSQL table name (e.g., 'acs_b01001')
        var_list: ordered list of variable_ids
        all_rows: list of dicts with 'geo_id', 'data_year', and variable values
    """
    if not all_rows:
        return 0

    col_names = ["geo_id", "data_year"] + [v.lower() for v in var_list]
    cols_sql = ", ".join(col_names)

    insert_sql = (
        f"INSERT INTO {pg_table} ({cols_sql}) VALUES %s "
        f"ON CONFLICT (geo_id, data_year) DO NOTHING"
    )

    tuples = []
    for row in all_rows:
        vals = [row["geo_id"], row["data_year"]]
        for var_id in var_list:
            vals.append(row.get(var_id))
        tuples.append(tuple(vals))

    with conn.cursor() as cur:
        execute_values(cur, insert_sql, tuples, page_size=2000)
    conn.commit()
    return len(tuples)


def load_one_table(table_id, table_info, table_idx, total_tables):
    """Load all years of data for a single ACS table.

    This function is called from the main loop (not parallelized at the
    table level — parallelism is at the API-call level within fetch_table_year).
    Each table gets its own DB connection + transaction.
    """
    var_list = table_info["vars"]
    title = table_info["title"]
    log.info(
        "Loading table %s (%s) [%d/%d] — %d variables",
        table_id, title[:60], table_idx, total_tables, len(var_list),
    )

    conn = get_connection()
    try:
        # Create the table (drop + create)
        pg_table = create_acs_table(conn, table_id, var_list)

        total_inserted = 0
        for year in YEARS:
            try:
                rows = fetch_table_year(table_id, var_list, year)
                if not rows:
                    log.debug("  %s/%d: no data returned", table_id, year)
                    continue

                n = insert_table_data(conn, pg_table, var_list, rows)
                total_inserted += n
                log.debug("  %s/%d: %d rows inserted", table_id, year, n)

            except Exception as exc:
                log.warning(
                    "  %s/%d FAILED: %s — rolling back year, continuing",
                    table_id, year, exc,
                )
                conn.rollback()
                continue

        if total_inserted > 0:
            log.info(
                "  %s done: %d total rows across %d years into %s",
                table_id, total_inserted, len(YEARS), pg_table,
            )
        else:
            log.warning("  %s: no data loaded for any year", table_id)

    except Exception as exc:
        log.error("  %s TABLE-LEVEL FAILURE: %s", table_id, exc)
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    start_time = time.time()
    log.info("=" * 70)
    log.info("ACS 5-Year Full ETL — Loading ALL Detailed Tables")
    log.info("Years: %s", YEARS)
    log.info("=" * 70)

    conn = get_connection()

    # Step 1: Catalog
    log.info("")
    log.info("STEP 1: Populate acs_catalog")
    log.info("-" * 40)
    load_catalog(conn)

    # Step 2: Geographies
    log.info("")
    log.info("STEP 2: Populate geographies")
    log.info("-" * 40)
    load_geographies(conn)

    # Step 3: Load data tables
    log.info("")
    log.info("STEP 3: Load ACS data tables")
    log.info("-" * 40)

    tables = get_table_variables(conn)
    conn.close()

    total = len(tables)
    log.info("Found %d unique table groups to load.", total)

    # Process tables sequentially. Parallelism happens at the API fetch level
    # inside fetch_table_year (via batched requests). Doing table-level
    # parallelism would create too many DB connections and API requests.
    for idx, (table_id, table_info) in enumerate(sorted(tables.items()), 1):
        load_one_table(table_id, table_info, idx, total)

    elapsed = time.time() - start_time
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    log.info("")
    log.info("=" * 70)
    log.info("ETL complete in %dh %dm. Loaded %d tables x %d years.",
             hours, minutes, total, len(YEARS))
    log.info("=" * 70)


if __name__ == "__main__":
    main()
