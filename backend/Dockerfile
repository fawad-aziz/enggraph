FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    fastapi uvicorn pydantic pydantic-settings neo4j \
    "sqlalchemy>=2.0" "psycopg[binary]" aiosqlite httpx alembic mcp

COPY enggraph/ ./enggraph/
COPY scripts/ ./scripts/
COPY alembic.ini ./
# The entrypoint runs `alembic upgrade head` from WORKDIR /app, and
# `script_location = alembic` in alembic.ini is resolved relative to the current
# directory. So the migrations directory must be copied next to the ini, or the
# first command of every container start fails with
# `FAILED: Path doesn't exist: alembic`. tests/test_compose_contract.py checks the
# ini's script_location against the build context and against these COPY lines.
COPY alembic/ ./alembic/

ENV PYTHONUNBUFFERED=1
# The image never pip-installs this package: dependencies are installed, then the
# source is copied. `python scripts/create_user.py` puts the script's own directory
# (/app/scripts) on sys.path, not /app, so `import enggraph` fails with
# ModuleNotFoundError. PYTHONPATH=/app makes the package importable from any working
# directory, so `python scripts/X.py` and `python -m scripts.X` both run.
ENV PYTHONPATH=/app

CMD ["sh", "/app/scripts/docker_entrypoint.sh"]
