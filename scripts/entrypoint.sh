#!/bin/bash
set -e

echo "=== Data Agent Bootstrap ==="

# Step 1: Load ACS data (idempotent — skips if already loaded)
# Check if data exists by querying geographies
ROW_COUNT=$(python -c "
import psycopg2, os
try:
    conn = psycopg2.connect(
        host=os.getenv('POSTGRES_HOST','localhost'),
        port=os.getenv('POSTGRES_PORT','5432'),
        user=os.getenv('POSTGRES_USER','census_admin'),
        password=os.getenv('POSTGRES_PASSWORD','census_pass'),
        dbname=os.getenv('POSTGRES_DB','census_data'))
    cur = conn.cursor()
    cur.execute('SELECT count(*) FROM geographies')
    print(cur.fetchone()[0])
    conn.close()
except:
    print(0)
" 2>/dev/null)

if [ "$ROW_COUNT" -gt "0" ] 2>/dev/null; then
    echo "Data already loaded ($ROW_COUNT geographies). Skipping data load."
else
    # Priority: local bulk files > Census API > error
    if [ -d "/app/data/raw/acs" ]; then
        echo "Local ACS summary files found — running bulk file ETL..."
        python scripts/load_from_files.py
        echo "  Bulk ETL complete."
    elif [ -n "$CENSUS_API_KEY" ]; then
        echo "CENSUS_API_KEY found — running full ACS ETL from Census API..."
        python scripts/load_all_acs.py
        echo "  API ETL complete."
    else
        echo "ERROR: No data source available."
        echo "  Option 1: Download bulk files with 'bash scripts/download_acs_bulk.sh'"
        echo "  Option 2: Set CENSUS_API_KEY in .env"
        exit 1
    fi
fi

# Step 2: Setup MindsDB connection (idempotent — drops and recreates)
python scripts/setup_mindsdb.py

# Step 3: Start Chainlit
echo "Starting Chainlit..."
exec chainlit run src/app.py --host 0.0.0.0 --port ${PORT:-8000}
