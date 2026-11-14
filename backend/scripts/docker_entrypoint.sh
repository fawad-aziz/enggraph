#!/bin/sh
set -e
# Apply application DB migrations (repeatable; safe on restarts).
alembic upgrade head
# Install graph schema (constraints + indexes); repeatable.
python -m enggraph.install_schema
exec uvicorn enggraph.main:app --host 0.0.0.0 --port "${BACKEND_PORT:-8000}"
