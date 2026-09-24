#!/bin/sh
set -eu
if [ -f /run/secrets/pg_password ]; then
  pw=$(cat /run/secrets/pg_password)
  export DATABASE_URL="postgresql+asyncpg://assurance:${pw}@db:5432/assurance"
fi
test -f /src/backend/alembic.ini
test -d /src/backend/migrations/versions
cd /src/backend
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
