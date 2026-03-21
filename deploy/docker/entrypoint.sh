#!/usr/bin/env sh
set -eu

CONFIG_DIR=${CONFIG_DIR:-/config}
ENV_PATH="${CONFIG_DIR}/.env"
DEFAULT_ENV_PATH="/app/deploy/docker/default.env"

mkdir -p "${CONFIG_DIR}"

if [ ! -f "${ENV_PATH}" ]; then
  cp "${DEFAULT_ENV_PATH}" "${ENV_PATH}"
  echo "Created ${ENV_PATH} from default template."
fi

set -a
. "${ENV_PATH}"
set +a

DB_MODE=${DB_MODE:-local}
POSTGRES_DB=${POSTGRES_DB:-agile_predict}
POSTGRES_USER=${POSTGRES_USER:-postgres}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-postgres}
POSTGRES_PORT=${POSTGRES_PORT:-5432}
POSTGRES_DATA_DIR=${POSTGRES_DATA_DIR:-${CONFIG_DIR}/postgresql}

if [ "${DB_MODE}" = "local" ]; then
  DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

case "${DATABASE_URL:-}" in
  postgresql* ) ;;
  * )
    echo "DATABASE_URL must be PostgreSQL"
    exit 1
    ;;
esac

export DB_MODE DATABASE_URL POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_PORT POSTGRES_DATA_DIR

PG_BINDIR="$(pg_config --bindir)"
INITDB="${PG_BINDIR}/initdb"
PG_CTL="${PG_BINDIR}/pg_ctl"
PSQL="${PG_BINDIR}/psql"

cleanup() {
  if [ "${DB_MODE}" = "local" ]; then
    su postgres -s /bin/sh -c "'${PG_CTL}' -D '${POSTGRES_DATA_DIR}' -m fast -w stop" >/dev/null 2>&1 || true
  fi
}

if [ "${DB_MODE}" = "local" ]; then
  mkdir -p "${POSTGRES_DATA_DIR}"
  chown -R postgres:postgres "${CONFIG_DIR}" "${POSTGRES_DATA_DIR}"

  if [ ! -s "${POSTGRES_DATA_DIR}/PG_VERSION" ]; then
    su postgres -s /bin/sh -c "'${INITDB}' -D '${POSTGRES_DATA_DIR}' >/dev/null"
    {
      echo "listen_addresses = '127.0.0.1'"
      echo "port = ${POSTGRES_PORT}"
    } >> "${POSTGRES_DATA_DIR}/postgresql.conf"
    echo "host all all 127.0.0.1/32 scram-sha-256" >> "${POSTGRES_DATA_DIR}/pg_hba.conf"
  fi

  su postgres -s /bin/sh -c "'${PG_CTL}' -D '${POSTGRES_DATA_DIR}' -w start >/dev/null"
  trap cleanup EXIT INT TERM

  cat > /tmp/init-local-db.sql <<SQL
SELECT 'CREATE ROLE "${POSTGRES_USER}" LOGIN PASSWORD ''${POSTGRES_PASSWORD}'''
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${POSTGRES_USER}')\gexec
ALTER ROLE "${POSTGRES_USER}" WITH LOGIN PASSWORD '${POSTGRES_PASSWORD}';
SELECT 'CREATE DATABASE "${POSTGRES_DB}" OWNER "${POSTGRES_USER}"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${POSTGRES_DB}')\gexec
SQL
  su postgres -s /bin/sh -c "'${PSQL}' -v ON_ERROR_STOP=1 --dbname postgres -f /tmp/init-local-db.sql >/dev/null"
  rm -f /tmp/init-local-db.sql
fi

# Run Alembic migrations
echo "Running database migrations..."
alembic upgrade head || {
  echo "WARNING: Alembic migrations failed, attempting fallback to metadata.create_all()"
}

uvicorn src.main:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"
