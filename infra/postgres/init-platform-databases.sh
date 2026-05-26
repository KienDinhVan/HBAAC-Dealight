#!/bin/sh
set -eu

host="${PGHOST:-/var/run/postgresql}"
port="${PGPORT:-5432}"
user="${POSTGRES_USER:-forecast}"
main_db="${POSTGRES_DB:-sku_forecasting}"

export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be provided}"

create_database() {
    database="$1"
    if ! psql -h "$host" -p "$port" -U "$user" -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname = '$database'" | grep -q 1; then
        psql -h "$host" -p "$port" -U "$user" -d postgres -v ON_ERROR_STOP=1 \
            -c "CREATE DATABASE \"$database\" OWNER \"$user\""
    fi
}

create_database mlflow
create_database airflow

if [ -f /opt/platform/init_db.sql ]; then
    psql -h "$host" -p "$port" -U "$user" -d "$main_db" -v ON_ERROR_STOP=1 \
        -f /opt/platform/init_db.sql
fi
