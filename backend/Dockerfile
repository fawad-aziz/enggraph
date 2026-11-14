FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    fastapi uvicorn pydantic pydantic-settings neo4j \
    "sqlalchemy>=2.0" "psycopg[binary]" aiosqlite httpx alembic mcp

COPY enggraph/ ./enggraph/
COPY scripts/ ./scripts/
COPY alembic.ini ./
# Migrations directory is created in milestone 4; tolerate absence during early builds.

ENV PYTHONUNBUFFERED=1

CMD ["sh", "/app/scripts/docker_entrypoint.sh"]
