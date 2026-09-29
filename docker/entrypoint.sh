#!/bin/sh
# API container entrypoint: apply migrations (and optionally seed demo data), then start the server.
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "Applying database migrations..."
    alembic upgrade head
fi

if [ "${SEED_ON_STARTUP:-true}" = "true" ]; then
    echo "Seeding demo data (idempotent)..."
    python -m app.scripts.seed
fi

exec "$@"
