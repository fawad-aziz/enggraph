#!/bin/sh
set -e
# Apply application DB migrations (repeatable; safe on restarts).
# `script_location = alembic` in alembic.ini is resolved relative to this working
# directory, so an image built without the migrations directory dies at alembic with
# `FAILED: Path doesn't exist: alembic` and exit 255, which names nothing you can act
# on. A stale image is the common case: `docker compose up` reuses the image it
# already built, so editing the Dockerfile has no effect until the image is rebuilt.
# Refuse with the fix named.
if [ ! -f alembic/env.py ]; then
    echo "refused: the migrations directory is not in the image at /app/alembic. Rebuild the images: docker compose build backend worker mcp-server"
    exit 2
fi
alembic upgrade head
# Install graph schema (constraints + indexes); repeatable.
python -m enggraph.install_schema
exec uvicorn enggraph.main:app --host 0.0.0.0 --port "${BACKEND_PORT:-8000}"
