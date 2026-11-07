#!/usr/bin/env bash
set -e

echo "Waiting for database..."
python - <<'PY'
import os, time, psycopg

url = os.environ["DIRECTHOME_DATABASE_URL"].replace(
    "postgresql+psycopg://", "postgresql://"
)
for _ in range(60):
    try:
        psycopg.connect(url, connect_timeout=2).close()
        print("Database is up.")
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("Database not reachable after 60s")
PY

echo "Running migrations..."
alembic upgrade head

echo "Starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${UVICORN_WORKERS:-2}"
