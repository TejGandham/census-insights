"""ETL: Load ACS 5-Year data from local Census Summary Files into PostgreSQL.

Replaces the API-based load_all_acs.py when bulk files are available locally.
Expects files downloaded by scripts/download_acs_bulk.sh into data/raw/acs/.

Three-step process:
  1. Populate acs_catalog from Census variables.json (no API key needed)
  2. Populate geographies from local geo files (states + counties + tracts)
  3. For each ACS table, parse .dat files and bulk-insert into PostgreSQL

Usage:
    python scripts/load_from_files.py

Requires:
    PostgreSQL running with schema from init_db.sql already applied.
    Local files in data/raw/acs/{year}/ (from download_acs_bulk.sh).
"""

import csv
import glob
import logging
import os
import re
import sys
import time
from multiprocessing import Pool

import psycopg2
from psycopg2.extras import execute_values
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = os.getenv("POSTGRES_PORT", "5432")
PG_USER = os.getenv("POSTGRES_USER", "census_admin")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "census_pass")
PG_DB = os.getenv("POSTGRES_DB", "census_data")

DATA_DIR = os.getenv("ACS_DATA_DIR", "data/raw/acs")
YEARS = [2019, 2020, 2021, 2022, 2023]
WORKERS = int(os.getenv("ETL_WORKERS", "4"))

# GEO_ID prefixes we keep (state, county, tract)
KEEP_PREFIXES = ("0400000US", "0500000US", "1400000US")

# Census API sentinel values that mean "data not available"
CENSUS_SENTINELS = {
    -666666666, -555555555, -333333333, -222222222, -999999999,
    -666666666.0, -555555555.0, -333333333.0, -222222222.0, -999999999.0,
}

# Variables / prefixes to skip when parsing variables.json
SKIP_VARIABLE_NAMES = {
    "for", "in", "ucgid", "GEO_ID", "NAME", "GEOCOMP",
    "SUMLEVEL", "STATE", "COUNTY", "PLACE", "TRACT", "BLKGRP",
}
ANNOTATION_SUFFIXES = ("EA", "MA", "PEA", "PMA")
VALID_TABLE_PREFIXES = ("B", "C")

VARIABLES_JSON_URL = "https://api.census.gov/data/2023/acs/acs5/variables.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database helpers (same as load_all_acs.py)
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


def safe_numeric(val):
    """Convert a Census value to a Python numeric, or None.

    Handles sentinel values, empty strings, None, non-numeric strings,
    and the '*****' annotation used for margins of error.
    """
    if val is None or val == "" or val == "null" or val == "*****":
        return None
    try:
        f = float(val)
        if f in CENSUS_SENTINELS or f < -99_999_999:
            return None
        if f == int(f) and abs(f) < 1e15:
            return int(f)
        return f
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Step 1: Populate acs_catalog (reuses variables.json — no API key needed)
# ---------------------------------------------------------------------------
def load_catalog(conn):
    """Fetch variables.json and populate the acs_catalog table."""
    if table_has_rows(conn, "acs_catalog"):
        log.info("acs_catalog already populated — skipping.")
        return

    log.info("Fetching variables.json from Census API (~30 MB, no key needed)...")
    try:
        resp = requests.get(VARIABLES_JSON_URL, timeout=120)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Failed to fetch variables.json: %s", exc)
        sys.exit(1)

    variables = data.get("variables", {})
    log.info("Parsing %d raw variable entries...", len(variables))

    rows = []
    for var_name, meta in variables.items():
        if var_name.upper() in SKIP_VARIABLE_NAMES or var_name in SKIP_VARIABLE_NAMES:
            continue
        if any(var_name.endswith(suffix) for suffix in ANNOTATION_SUFFIXES):
            continue

        group = meta.get("group", "N/A")
        if group == "N/A" or not group:
            continue
        if not any(group.startswith(p) for p in VALID_TABLE_PREFIXES):
            continue

        if var_name.endswith("E"):
            is_estimate = True
        elif var_name.endswith("M"):
            is_estimate = False
        else:
            continue

        label = meta.get("label", "")
        concept = meta.get("concept", "")

        rows.append((
            group,
            var_name,
            label,
            concept,
            "",
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
# Step 2: Populate geographies from local geo files
# ---------------------------------------------------------------------------
def _parse_geo_id(geo_id):
    """Parse a Census GEO_ID into components.

    Returns dict with: geo_id, sumlevel, state_fips, county_fips, tract_code
    or None if the geo_id doesn't match a kept sumlevel.
    """
    if geo_id.startswith("0400000US"):
        # State: 0400000US{state_fips}
        state_fips = geo_id[9:11]
        return {
            "sumlevel": "040",
            "state_fips": state_fips,
            "county_fips": None,
            "tract_code": None,
        }
    elif geo_id.startswith("0500000US"):
        # County: 0500000US{state_fips}{county_fips}
        state_fips = geo_id[9:11]
        county_fips = geo_id[11:14]
        return {
            "sumlevel": "050",
            "state_fips": state_fips,
            "county_fips": county_fips,
            "tract_code": None,
        }
    elif geo_id.startswith("1400000US"):
        # Tract: 1400000US{state_fips}{county_fips}{tract_code}
        state_fips = geo_id[9:11]
        county_fips = geo_id[11:14]
        tract_code = geo_id[14:20]
        return {
            "sumlevel": "140",
            "state_fips": state_fips,
            "county_fips": county_fips,
            "tract_code": tract_code,
        }
    return None


def _detect_delimiter(filepath):
    """Detect whether a file uses pipe or comma delimiter."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        first_line = f.readline()
    if "|" in first_line:
        return "|"
    return ","


def _load_state_names(conn):
    """Load existing state FIPS → name mapping from geographies table.

    Returns empty dict if table has no data yet (first load).
    """
    mapping = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state_fips, state_name FROM geographies "
                "WHERE sumlevel = '040' AND state_name IS NOT NULL"
            )
            for fips, name in cur.fetchall():
                mapping[fips] = name
    except Exception:
        conn.rollback()
    return mapping


def load_geographies(conn):
    """Parse local geography files and populate the geographies table.

    Reads geos.csv (2019-2020) or geos.txt (2021+) for each year.
    Filters to sumlevels 040, 050, 140. Deduplicates across years via UPSERT.
    """
    if table_has_rows(conn, "geographies"):
        log.info("geographies already populated — skipping.")
        return

    all_geos = {}  # geo_id -> row dict (dedup across years)

    for year in YEARS:
        # Find the geo file for this year
        geo_path = None
        for ext in ("csv", "txt"):
            candidate = os.path.join(DATA_DIR, str(year), f"geos.{ext}")
            if os.path.exists(candidate):
                geo_path = candidate
                break

        if not geo_path:
            log.warning("No geo file found for year %d — skipping.", year)
            continue

        delimiter = _detect_delimiter(geo_path)
        log.info("Parsing geo file: %s (delimiter=%r)", geo_path, delimiter)

        with open(geo_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                geo_id = row.get("GEO_ID", row.get("GEOID", "")).strip()
                if not geo_id:
                    continue

                parsed = _parse_geo_id(geo_id)
                if parsed is None:
                    continue  # Not a kept sumlevel

                name = row.get("NAME", "").strip()
                if not name:
                    name = geo_id  # Fallback

                all_geos[geo_id] = {
                    "geo_id": geo_id,
                    "name": name,
                    **parsed,
                }

    if not all_geos:
        log.error("No geographies parsed from any year. Check data/raw/acs/ files.")
        sys.exit(1)

    # Derive state_name and county_name from the name field
    # State names: build from sumlevel 040 entries
    state_names = {}
    for geo in all_geos.values():
        if geo["sumlevel"] == "040":
            state_names[geo["state_fips"]] = geo["name"]

    rows = []
    for geo in all_geos.values():
        state_name = state_names.get(geo["state_fips"])

        if geo["sumlevel"] == "040":
            county_name = None
        elif geo["sumlevel"] == "050":
            # "County Name, State Name"
            parts = geo["name"].split(", ", 1)
            county_name = parts[0] if parts else geo["name"]
        elif geo["sumlevel"] == "140":
            # Tract names are like "Census Tract 1.01, County, State"
            # or "Census Tract 1.01; County; State"
            county_name = None  # tracts don't have a simple county_name
        else:
            county_name = None

        rows.append((
            geo["geo_id"],
            geo["name"],
            geo["sumlevel"],
            geo["state_fips"],
            geo["county_fips"],
            geo["tract_code"],
            state_name,
            county_name,
        ))

    log.info(
        "Inserting %d geographies (states=%d, counties=%d, tracts=%d)...",
        len(rows),
        sum(1 for g in all_geos.values() if g["sumlevel"] == "040"),
        sum(1 for g in all_geos.values() if g["sumlevel"] == "050"),
        sum(1 for g in all_geos.values() if g["sumlevel"] == "140"),
    )

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
# Step 3: Load ACS data tables from .dat files
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


# Cache: year -> directory containing .dat files (discovered once per year)
_dat_dir_cache = {}


def _find_dat_dir(year):
    """Discover the directory containing .dat files for a given year.

    Census zips have inconsistent nesting (flat for 2022, deeply nested for others).
    We search once per year for a known reference file, then cache the directory.
    """
    if year in _dat_dir_cache:
        return _dat_dir_cache[year]

    # Look for the b01001 file as a reference
    ref_name = f"acsdt5y{year}-b01001.dat"
    year_dir = os.path.join(DATA_DIR, str(year))

    for root, _dirs, files in os.walk(year_dir):
        if ref_name in files:
            _dat_dir_cache[year] = root
            log.info("  Year %d .dat directory: %s", year, root)
            return root

    _dat_dir_cache[year] = None
    return None


def _find_dat_file(year, table_id):
    """Find the .dat file for a table+year in the data directory.

    Census uses: acsdt5y{year}-{table_id_lower}.dat
    """
    dat_dir = _find_dat_dir(year)
    if dat_dir is None:
        return None

    filename = f"acsdt5y{year}-{table_id.lower()}.dat"
    path = os.path.join(dat_dir, filename)
    if os.path.exists(path):
        return path
    return None


def _catalog_to_file_col(var_id):
    """Convert catalog variable ID to .dat file column name.

    Catalog format: B01001_001E  (table_numSuffix)
    File format:    B01001_E001  (table_SuffixNum)
    """
    # Split on underscore: "B01001_001E" -> "B01001", "001E"
    parts = var_id.rsplit("_", 1)
    if len(parts) != 2 or len(parts[1]) < 2:
        return var_id
    table_part = parts[0]
    num_suffix = parts[1]  # e.g., "001E"
    suffix = num_suffix[-1]  # "E" or "M"
    num = num_suffix[:-1]    # "001"
    return f"{table_part}_{suffix}{num}"


def _parse_dat_file(filepath, var_list, year):
    """Parse a pipe-delimited .dat file and return filtered rows.

    Reads only rows with GEO_ID prefixes we keep (state/county/tract).
    Extracts estimate columns matching var_list.

    Returns:
        list of dicts: [{"geo_id": ..., "data_year": ..., var_id: numeric_val, ...}]
    """
    rows = []

    # Build mapping: catalog var_id -> file column name
    # Catalog: B01001_001E -> File: B01001_E001
    var_to_file_col = {}
    for var_id in var_list:
        var_to_file_col[var_id] = _catalog_to_file_col(var_id)

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="|")
        if reader.fieldnames is None:
            return rows

        # Build a set of column names present in the file (uppercase for matching)
        file_cols = {c.upper(): c for c in reader.fieldnames}

        for record in reader:
            geo_id = record.get("GEO_ID", "").strip()
            if not geo_id or not geo_id.startswith(KEEP_PREFIXES):
                continue

            row = {"geo_id": geo_id, "data_year": year}
            for var_id in var_list:
                file_col = var_to_file_col[var_id]
                # Try the file column name directly, then case-insensitive lookup
                raw = record.get(file_col) or record.get(file_cols.get(file_col.upper(), ""))
                row[var_id] = safe_numeric(raw)
            rows.append(row)

    return rows


def create_acs_table(conn, table_id, var_list):
    """DROP + CREATE TABLE for one ACS data table."""
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
    """Bulk-insert rows into an ACS data table."""
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


def _load_one_table(args):
    """Load all years of data for a single ACS table from .dat files.

    Called by multiprocessing.Pool — accepts a single tuple arg.
    Each worker gets its own DB connection.
    """
    table_id, var_list, title, table_idx, total_tables = args

    log.info(
        "Loading table %s (%s) [%d/%d] — %d variables",
        table_id, title[:60], table_idx, total_tables, len(var_list),
    )

    conn = get_connection()
    try:
        pg_table = create_acs_table(conn, table_id, var_list)

        total_inserted = 0
        for year in YEARS:
            dat_path = _find_dat_file(year, table_id)
            if dat_path is None:
                log.debug("  %s/%d: no .dat file found", table_id, year)
                continue

            try:
                rows = _parse_dat_file(dat_path, var_list, year)
                if not rows:
                    log.debug("  %s/%d: no matching rows after filtering", table_id, year)
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
    log.info("ACS 5-Year Bulk File ETL — Loading from local Summary Files")
    log.info("Data directory: %s", DATA_DIR)
    log.info("Years: %s", YEARS)
    log.info("Workers: %d", WORKERS)
    log.info("=" * 70)

    # Verify data directory exists
    if not os.path.isdir(DATA_DIR):
        log.error(
            "Data directory not found: %s\n"
            "Run 'bash scripts/download_acs_bulk.sh' first to download Census files.",
            DATA_DIR,
        )
        sys.exit(1)

    # Verify at least one year has data
    found_years = []
    for year in YEARS:
        year_dir = os.path.join(DATA_DIR, str(year))
        if os.path.isdir(year_dir):
            found_years.append(year)
    if not found_years:
        log.error("No year directories found in %s. Download data first.", DATA_DIR)
        sys.exit(1)
    log.info("Found data for years: %s", found_years)

    conn = get_connection()

    # Step 1: Catalog
    log.info("")
    log.info("STEP 1: Populate acs_catalog")
    log.info("-" * 40)
    load_catalog(conn)

    # Step 2: Geographies
    log.info("")
    log.info("STEP 2: Populate geographies (from local geo files)")
    log.info("-" * 40)
    load_geographies(conn)

    # Step 3: Load data tables
    log.info("")
    log.info("STEP 3: Load ACS data tables (from .dat files)")
    log.info("-" * 40)

    tables = get_table_variables(conn)
    conn.close()

    total = len(tables)
    log.info("Found %d unique table groups to load.", total)

    # Build task list for multiprocessing
    tasks = []
    for idx, (table_id, table_info) in enumerate(sorted(tables.items()), 1):
        tasks.append((
            table_id,
            table_info["vars"],
            table_info["title"],
            idx,
            total,
        ))

    if WORKERS > 1:
        log.info("Using multiprocessing pool with %d workers.", WORKERS)
        with Pool(processes=WORKERS) as pool:
            pool.map(_load_one_table, tasks)
    else:
        log.info("Running sequentially (WORKERS=1).")
        for task in tasks:
            _load_one_table(task)

    elapsed = time.time() - start_time
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    log.info("")
    log.info("=" * 70)
    log.info(
        "ETL complete in %dh %dm. Loaded %d tables x %d years.",
        hours, minutes, total, len(YEARS),
    )
    log.info("=" * 70)


if __name__ == "__main__":
    main()
