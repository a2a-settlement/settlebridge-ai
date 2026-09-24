#!/bin/sh
set -eu
if [ -f /run/secrets/pg_password ]; then
  pw=$(cat /run/secrets/pg_password)
  export A2A_EXCHANGE_DATABASE_URL="postgresql+psycopg://assurance:${pw}@db:5432/assurance"
fi
export A2A_EXCHANGE_HOST="${A2A_EXCHANGE_HOST:-0.0.0.0}"
export A2A_EXCHANGE_PORT="${A2A_EXCHANGE_PORT:-8000}"
exec a2a-exchange
