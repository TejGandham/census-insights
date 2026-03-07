-- US Census ACS 5-Year — Full Summary File Schema
-- Replaces old 5-table hand-curated schema with support for ALL ACS tables.
--
-- Foundation tables (geographies, acs_catalog) created here.
-- ACS data tables (~1,193 per year) are auto-created by load_summary_files.py.

-- ============================================================
-- WIPE: Remove old hand-curated schema
-- ============================================================
DROP TABLE IF EXISTS demographics CASCADE;
DROP TABLE IF EXISTS economics CASCADE;
DROP TABLE IF EXISTS education CASCADE;
DROP TABLE IF EXISTS housing CASCADE;
DROP TABLE IF EXISTS geo_areas CASCADE;

-- Drop all auto-created ACS data tables from previous runs
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (SELECT tablename FROM pg_tables
              WHERE schemaname = 'public' AND tablename LIKE 'acs_%')
    LOOP
        EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) || ' CASCADE';
    END LOOP;
END $$;

-- Drop foundation tables for clean re-run
DROP TABLE IF EXISTS acs_catalog CASCADE;
DROP TABLE IF EXISTS geographies CASCADE;

-- Clean up old role (ignore errors if it doesn't exist)
DO $$ BEGIN
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM census_reader';
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
DO $$ BEGIN
    DROP ROLE IF EXISTS census_reader;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- ============================================================
-- 1. GEOGRAPHIES — parsed from Census geography file
-- ============================================================
-- Source: Census geo file or API state/county/tract endpoints.
-- We filter to sumlevel 040 (state), 050 (county), and 140 (tract).
CREATE TABLE geographies (
    geo_id      VARCHAR(60) PRIMARY KEY,
    name        VARCHAR(300) NOT NULL,
    sumlevel    VARCHAR(3)  NOT NULL,     -- '040'=state, '050'=county, '140'=tract
    state_fips  VARCHAR(2),
    county_fips VARCHAR(3),
    tract_code  VARCHAR(6),              -- 6-digit tract code (sumlevel 140 only)
    state_name  VARCHAR(100),
    county_name VARCHAR(200),
    area_type   VARCHAR(20) GENERATED ALWAYS AS (
        CASE sumlevel
            WHEN '040' THEN 'state'
            WHEN '050' THEN 'county'
            WHEN '140' THEN 'tract'
            ELSE 'other'
        END
    ) STORED
);

CREATE INDEX idx_geo_sumlevel  ON geographies(sumlevel);
CREATE INDEX idx_geo_state     ON geographies(state_fips);
CREATE INDEX idx_geo_county    ON geographies(state_fips, county_fips);
CREATE INDEX idx_geo_area_type ON geographies(area_type);

-- ============================================================
-- 2. ACS CATALOG — variable metadata for agent discovery
-- ============================================================
-- Source: Census API /variables.json endpoint.
-- The LLM agent searches this table to find which ACS table
-- contains the columns it needs before querying data.
CREATE TABLE acs_catalog (
    table_id    VARCHAR(20) NOT NULL,       -- e.g. 'B01001'
    variable_id VARCHAR(30) PRIMARY KEY,    -- e.g. 'B01001_001E'
    label       TEXT NOT NULL,              -- e.g. 'Estimate!!Total:'
    table_title TEXT,                       -- e.g. 'SEX BY AGE'
    universe    TEXT,                       -- e.g. 'Total population'
    is_estimate BOOLEAN DEFAULT TRUE        -- TRUE=estimate, FALSE=margin of error
);

CREATE INDEX idx_catalog_table     ON acs_catalog(table_id);
CREATE INDEX idx_catalog_title_fts ON acs_catalog
    USING gin(to_tsvector('english', table_title));
CREATE INDEX idx_catalog_label_fts ON acs_catalog
    USING gin(to_tsvector('english', label));

-- ============================================================
-- NOTES: Auto-created ACS data tables
-- ============================================================
-- load_all_acs.py creates one PG table per ACS table group, e.g.:
--
--   CREATE TABLE acs_b01001 (
--       geo_id    VARCHAR(60) REFERENCES geographies(geo_id),
--       data_year INTEGER NOT NULL,
--       b01001_001e NUMERIC,   -- estimate columns (lowercase variable IDs)
--       b01001_002e NUMERIC,
--       ...
--       PRIMARY KEY (geo_id, data_year)
--   );
--
-- The agent queries tables through MindsDB: census_db.acs_b01001
