"""Setup MindsDB: create PostgreSQL database connection.

The agent queries ACS data tables and the catalog directly through
this connection. No views or MindsDB agents are created — the
OpenAI agent in agent_client.py handles tool calling directly.

Usage:
    python scripts/setup_mindsdb.py
"""

import os
import time

import mindsdb_sdk
from dotenv import load_dotenv

load_dotenv()

MINDSDB_HOST = os.getenv("MINDSDB_HOST", "http://localhost:47334")
PG_USER = os.getenv("POSTGRES_USER", "census_admin")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "census_pass")
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = os.getenv("POSTGRES_PORT", "5432")
PG_DB = os.getenv("POSTGRES_DB", "census_data")


def wait_for_mindsdb(server, retries=30, delay=5):
    for i in range(retries):
        try:
            server.list_databases()
            return True
        except Exception:
            print(f"  Waiting for MindsDB... ({i+1}/{retries})")
            time.sleep(delay)
    raise RuntimeError("MindsDB not available")


def main():
    print(f"Connecting to MindsDB at {MINDSDB_HOST}...")
    server = mindsdb_sdk.connect(MINDSDB_HOST)
    wait_for_mindsdb(server)
    print("  Connected.")

    # Drop old census_db connection if it exists
    print("Creating PostgreSQL connection (census_db)...")
    try:
        server.drop_database("census_db")
    except Exception:
        pass

    server.create_database(
        "census_db",
        engine="postgres",
        connection_args={
            "user": PG_USER,
            "password": PG_PASS,
            "host": PG_HOST,
            "port": PG_PORT,
            "database": PG_DB,
        },
    )
    print("  census_db connected.")

    # Clean up old views from previous schema (if any)
    for old_view in ("demographics", "economics", "housing", "education", "census_data"):
        try:
            server.query(f"DROP VIEW IF EXISTS mindsdb.{old_view}").fetch()
        except Exception:
            pass

    print("\nSetup complete. Agent queries census_db.acs_* tables directly.")


if __name__ == "__main__":
    main()
