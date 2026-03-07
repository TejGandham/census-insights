"""ETL: Pull US Census ACS 5-Year data (multi-year) and load into PostgreSQL.

Usage:
    python scripts/load_census_data.py

Requires CENSUS_API_KEY and Postgres env vars.
"""

import os

import psycopg2
from psycopg2.extras import execute_values
from census import Census
from dotenv import load_dotenv

load_dotenv()

CENSUS_API_KEY = os.environ["CENSUS_API_KEY"]
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = os.getenv("POSTGRES_PORT", "5432")
PG_USER = os.getenv("POSTGRES_USER", "census_admin")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "census_pass")
PG_DB = os.getenv("POSTGRES_DB", "census_data")

YEARS = [2019, 2020, 2021, 2022, 2023]

# ACS variable groups — see https://api.census.gov/data/2022/acs/acs5/variables.html
DEMO_VARS = [
    "NAME",
    "B01003_001E",  # total_population
    "B01002_001E",  # median_age
    "B01001_002E",  # male
    "B01001_026E",  # female
    "B09001_001E",  # under_18
    # 65+ male: 65-66, 67-69, 70-74, 75-79, 80-84, 85+
    "B01001_020E", "B01001_021E", "B01001_022E",
    "B01001_023E", "B01001_024E", "B01001_025E",
    # 65+ female: 65-66, 67-69, 70-74, 75-79, 80-84, 85+
    "B01001_044E", "B01001_045E", "B01001_046E",
    "B01001_047E", "B01001_048E", "B01001_049E",
    "B02001_002E",  # white
    "B02001_003E",  # black
    "B03003_003E",  # hispanic
    "B02001_005E",  # asian
]

ECON_VARS = [
    "NAME",
    "B19013_001E",  # median_household_income
    "B19301_001E",  # per_capita_income (RESTRICTED in views)
    "B23025_001E",  # population 16+ (labor force participation denominator)
    "B23025_005E",  # unemployed
    "B23025_002E",  # labor_force
    "B17001_002E",  # below_poverty
    "B17001_001E",  # poverty_universe
    "B22010_002E",  # snap_benefits (RESTRICTED)
    "B19055_002E",  # social_security_income (RESTRICTED)
    "B27010_001E",  # health_insurance_universe (civilian noninst pop)
    "B27010_002E",  # with_health_insurance
]

EDU_VARS = [
    "NAME",
    "B15003_002E",  # no_schooling (approx < HS)
    "B15003_017E",  # hs_diploma
    "B15003_019E",  # some_college_1yr
    "B15003_021E",  # associates
    "B15003_022E",  # bachelors
    "B15003_023E",  # masters
    "B15003_024E",  # professional
    "B15003_025E",  # doctorate
    "B15003_001E",  # edu_universe (25+)
    "B14001_002E",  # school_enrollment
    "B14001_008E",  # college_enrollment
    "B14001_001E",  # enrollment_universe
]

HOUSING_VARS = [
    "NAME",
    "B25001_001E",  # total_housing_units
    "B25002_002E",  # occupied_units
    "B25003_002E",  # owner_occupied
    "B25003_003E",  # renter_occupied
    "B25064_001E",  # median_rent
    "B25077_001E",  # median_home_value (RESTRICTED)
    "B25010_001E",  # avg_household_size
    "B25002_003E",  # vacant_units
    "B25018_001E",  # median_rooms
    "B25034_010E",  # built_before_1950 (approx)
]


def get_connection():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, dbname=PG_DB
    )


def fetch_acs(variables, year, state_fips="*", county="*"):
    """Fetch ACS 5-year data for all counties in a state (or all states)."""
    c = Census(CENSUS_API_KEY, year=year)
    return c.acs5.get(variables, {"for": f"county:{county}", "in": f"state:{state_fips}"})


def fetch_state_level(variables, year):
    """Fetch ACS 5-year data at state level."""
    c = Census(CENSUS_API_KEY, year=year)
    return c.acs5.get(variables, {"for": "state:*"})


def safe_int(val):
    if val is None or val == "":
        return None
    try:
        v = int(float(val))
        # Census uses large negative sentinels for missing data
        return None if v < -99999 else v
    except (ValueError, TypeError):
        return None


def safe_float(val):
    if val is None or val == "":
        return None
    try:
        v = float(val)
        return None if v < -99999 else v
    except (ValueError, TypeError):
        return None


def safe_pct(num, denom):
    n, d = safe_int(num), safe_int(denom)
    if n is None or d is None or d == 0:
        return None
    return round(100.0 * n / d, 2)


def load_geo_areas(conn, state_rows, county_rows):
    """Load geo_areas from state + county data (year-independent lookup table)."""
    rows = []

    for r in state_rows:
        geo_id = f"04000US{r['state']}"
        rows.append((
            geo_id, r["NAME"], r["state"], r["NAME"],
            None, None, "state"
        ))

    for r in county_rows:
        geo_id = f"05000US{r['state']}{r['county']}"
        name = r["NAME"]
        parts = name.split(", ")
        county_name = parts[0] if parts else name
        state_name = parts[1] if len(parts) > 1 else None
        rows.append((
            geo_id, name, r["state"], state_name,
            f"{r['state']}{r['county']}", county_name, "county"
        ))

    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO geo_areas (geo_id, name, state_fips, state_name,
               county_fips, county_name, area_type) VALUES %s
               ON CONFLICT (geo_id) DO NOTHING""",
            rows,
        )
    conn.commit()
    print(f"    geo_areas: {len(rows)} rows")


def load_demographics(conn, state_rows, county_rows, year):
    age_65_plus_vars = [
        "B01001_020E", "B01001_021E", "B01001_022E",
        "B01001_023E", "B01001_024E", "B01001_025E",
        "B01001_044E", "B01001_045E", "B01001_046E",
        "B01001_047E", "B01001_048E", "B01001_049E",
    ]

    rows = []
    for data, level in [(state_rows, "state"), (county_rows, "county")]:
        for r in data:
            if level == "state":
                geo_id = f"04000US{r['state']}"
            else:
                geo_id = f"05000US{r['state']}{r['county']}"

            total_pop = safe_int(r.get("B01003_001E"))
            age_65_plus = sum(safe_int(r.get(v)) or 0 for v in age_65_plus_vars)

            pct_w = safe_pct(r.get("B02001_002E"), r.get("B01003_001E"))
            pct_b = safe_pct(r.get("B02001_003E"), r.get("B01003_001E"))
            pct_h = safe_pct(r.get("B03003_003E"), r.get("B01003_001E"))
            pct_a = safe_pct(r.get("B02001_005E"), r.get("B01003_001E"))
            race_detail = (
                f"White:{pct_w}% Black:{pct_b}% Hispanic:{pct_h}% Asian:{pct_a}%"
            )

            rows.append((
                geo_id, year,
                total_pop,
                safe_float(r.get("B01002_001E")),
                safe_pct(r.get("B01001_002E"), r.get("B01003_001E")),
                safe_pct(r.get("B01001_026E"), r.get("B01003_001E")),
                safe_pct(r.get("B09001_001E"), r.get("B01003_001E")),
                safe_pct(age_65_plus, total_pop) if total_pop else None,
                safe_pct(r.get("B02001_002E"), r.get("B01003_001E")),
                safe_pct(r.get("B02001_003E"), r.get("B01003_001E")),
                safe_pct(r.get("B03003_003E"), r.get("B01003_001E")),
                safe_pct(r.get("B02001_005E"), r.get("B01003_001E")),
                race_detail,
                f"ANC-{r['state']}",
            ))

    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO demographics (geo_id, data_year, total_population, median_age,
               pct_male, pct_female, pct_under_18, pct_65_and_over,
               pct_white, pct_black, pct_hispanic, pct_asian,
               race_detail, ancestry_code) VALUES %s
               ON CONFLICT (geo_id, data_year) DO NOTHING""",
            rows,
        )
    conn.commit()
    print(f"    demographics: {len(rows)} rows")


def load_economics(conn, state_rows, county_rows, year):
    rows = []
    for data, level in [(state_rows, "state"), (county_rows, "county")]:
        for r in data:
            if level == "state":
                geo_id = f"04000US{r['state']}"
            else:
                geo_id = f"05000US{r['state']}{r['county']}"

            labor_force = safe_int(r.get("B23025_002E"))
            pop_16_plus = safe_int(r.get("B23025_001E"))
            poverty_universe = safe_int(r.get("B17001_001E"))
            unemployed = safe_int(r.get("B23025_005E"))
            hi_universe = safe_int(r.get("B27010_001E"))
            hi_covered = safe_int(r.get("B27010_002E"))

            rows.append((
                geo_id, year,
                safe_int(r.get("B19013_001E")),
                safe_int(r.get("B19301_001E")),
                safe_pct(unemployed, labor_force) if labor_force else None,
                safe_pct(r.get("B17001_002E"), poverty_universe) if poverty_universe else None,
                safe_pct(labor_force, pop_16_plus) if pop_16_plus else None,
                safe_int(r.get("B22010_002E")),
                safe_int(r.get("B19055_002E")),
                safe_pct(hi_covered, hi_universe) if hi_universe else None,
            ))

    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO economics (geo_id, data_year, median_household_income, per_capita_income,
               unemployment_rate, poverty_rate, labor_force_participation,
               snap_benefits, social_security_income, pct_health_insurance) VALUES %s
               ON CONFLICT (geo_id, data_year) DO NOTHING""",
            rows,
        )
    conn.commit()
    print(f"    economics: {len(rows)} rows")


def load_education(conn, state_rows, county_rows, year):
    rows = []
    for data, level in [(state_rows, "state"), (county_rows, "county")]:
        for r in data:
            if level == "state":
                geo_id = f"04000US{r['state']}"
            else:
                geo_id = f"05000US{r['state']}{r['county']}"

            universe = safe_int(r.get("B15003_001E"))
            bachelors = safe_int(r.get("B15003_022E")) or 0
            masters = safe_int(r.get("B15003_023E")) or 0
            professional = safe_int(r.get("B15003_024E")) or 0
            doctorate = safe_int(r.get("B15003_025E")) or 0
            grad_degree = masters + professional + doctorate
            enroll_universe = safe_int(r.get("B14001_001E"))

            rows.append((
                geo_id, year,
                safe_pct(r.get("B15003_002E"), universe),
                safe_pct(r.get("B15003_017E"), universe),
                safe_pct(r.get("B15003_019E"), universe),
                safe_pct(bachelors, universe) if universe else None,
                safe_pct(grad_degree, universe) if universe else None,
                safe_int(r.get("B14001_002E")),
                safe_pct(r.get("B14001_008E"), enroll_universe),
            ))

    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO education (geo_id, data_year, pct_less_than_hs, pct_hs_graduate,
               pct_some_college, pct_bachelors, pct_graduate_degree,
               school_enrollment, pct_in_college) VALUES %s
               ON CONFLICT (geo_id, data_year) DO NOTHING""",
            rows,
        )
    conn.commit()
    print(f"    education: {len(rows)} rows")


def load_housing(conn, state_rows, county_rows, year):
    rows = []
    for data, level in [(state_rows, "state"), (county_rows, "county")]:
        for r in data:
            if level == "state":
                geo_id = f"04000US{r['state']}"
            else:
                geo_id = f"05000US{r['state']}{r['county']}"

            total_units = safe_int(r.get("B25001_001E"))
            occupied = safe_int(r.get("B25002_002E"))
            vacant = safe_int(r.get("B25002_003E"))

            rows.append((
                geo_id, year,
                total_units,
                occupied,
                safe_pct(r.get("B25003_002E"), occupied) if occupied else None,
                safe_pct(r.get("B25003_003E"), occupied) if occupied else None,
                safe_int(r.get("B25064_001E")),
                safe_int(r.get("B25077_001E")),
                safe_float(r.get("B25010_001E")),
                safe_pct(vacant, total_units) if total_units else None,
                safe_float(r.get("B25018_001E")),
                safe_pct(r.get("B25034_010E"), total_units),
            ))

    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO housing (geo_id, data_year, total_housing_units, occupied_units,
               owner_occupied_pct, renter_occupied_pct, median_rent,
               median_home_value, avg_household_size, vacancy_rate, median_rooms,
               pct_built_before_1950) VALUES %s
               ON CONFLICT (geo_id, data_year) DO NOTHING""",
            rows,
        )
    conn.commit()
    print(f"    housing: {len(rows)} rows")


def data_already_loaded():
    """Check if census data has already been loaded (idempotent bootstrap)."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM demographics")
            count = cur.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


def main():
    if data_already_loaded():
        print("Census data already loaded. Skipping ETL.")
        return

    conn = get_connection()
    years_loaded = 0

    for year in YEARS:
        print(f"\n=== ACS 5-Year {year} ===")

        try:
            print(f"  Fetching state-level data...")
            state_demo = fetch_state_level(DEMO_VARS, year)
            state_econ = fetch_state_level(ECON_VARS, year)
            state_edu = fetch_state_level(EDU_VARS, year)
            state_housing = fetch_state_level(HOUSING_VARS, year)
            print(f"  Got {len(state_demo)} states")

            print(f"  Fetching county-level data (all US counties)...")
            county_demo = fetch_acs(DEMO_VARS, year)
            county_econ = fetch_acs(ECON_VARS, year)
            county_edu = fetch_acs(EDU_VARS, year)
            county_housing = fetch_acs(HOUSING_VARS, year)
            print(f"  Got {len(county_demo)} counties")

            print(f"  Loading into PostgreSQL...")
            # Load geo_areas every year — counties change across years
            # (e.g., Alaska's Chugach Census Area created 2020).
            # ON CONFLICT DO NOTHING keeps it idempotent.
            load_geo_areas(conn, state_demo, county_demo)

            load_demographics(conn, state_demo, county_demo, year)
            load_economics(conn, state_econ, county_econ, year)
            load_education(conn, state_edu, county_edu, year)
            load_housing(conn, state_housing, county_housing, year)
            print(f"  Year {year} complete.")
            years_loaded += 1

        except Exception as e:
            print(f"  ERROR for year {year}: {e}")
            conn.rollback()
            print(f"  Skipping year {year}, continuing...")
            continue

    conn.close()

    if years_loaded == 0:
        print("\nETL failed: no years loaded successfully.")
        raise SystemExit(1)

    print(f"\nETL complete. {years_loaded}/{len(YEARS)} years loaded.")


if __name__ == "__main__":
    main()
