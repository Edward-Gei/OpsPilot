#!/bin/sh
set -e

if [ "${RUN_MIGRATIONS}" = "1" ]; then
  alembic upgrade head
fi

exec "$@"
