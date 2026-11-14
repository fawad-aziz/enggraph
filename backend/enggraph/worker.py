"""Sync worker CLI: claims jobs from Postgres and runs ingestion into Neo4j.

Usage:
    python -m enggraph.worker            # poll the queue forever
    python -m enggraph.worker --once     # claim and run one job, then exit

The worker owns no credentials of its own: it reads connector configuration from
Postgres (a secret *reference*) and resolves the PAT from the environment at
call time. Config/startup failures are reported with clear messages and exit
codes, and nothing runs when the database or graph store is unreachable.
"""

from __future__ import annotations

import argparse
import time

from enggraph.config import load_settings, validate_runtime
from enggraph.graph.driver import open_driver
from enggraph.graph.neo4j_repo import Neo4jGraphRepository
from enggraph.logging_setup import get_logger, setup_logging
from enggraph.persistence.engine import build_engine, build_sessionmaker, db_is_reachable
from enggraph.sync.worker import production_client_factory, run_once

log = get_logger("enggraph.worker")

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_DB = 3
EXIT_GRAPH = 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="enggraph.worker",
        description="Run the EngGraph sync worker (job queue consumer).",
    )
    parser.add_argument("--once", action="store_true", help="run one queued job and exit")
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="idle poll interval")
    parser.add_argument("--worker-id", default="worker-1")
    args = parser.parse_args(argv)
    setup_logging()

    settings = load_settings()
    problems = validate_runtime(settings)
    for problem in problems:
        log.error("configuration problem: %s", problem)
    if problems:
        return EXIT_CONFIG

    engine = build_engine(settings.postgres_dsn)
    if not db_is_reachable(engine):
        log.error("database unreachable — worker cannot claim jobs")
        return EXIT_DB
    driver = open_driver(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    if driver is None:
        log.error("neo4j unreachable — graph writes would be lost")
        engine.dispose()
        return EXIT_GRAPH

    sessionmaker = build_sessionmaker(engine)
    repo = Neo4jGraphRepository(driver)
    factory = production_client_factory(settings)
    ran = 0
    try:
        while True:
            with sessionmaker() as session:
                job = run_once(session, args.worker_id, client_factory=factory, repo=repo)
            if job is not None:
                ran += 1
                log.info(
                    "job finished",
                    extra={"job_id": job.id, "state": job.state, "mode": job.mode},
                )
            if args.once:
                break
            if job is None:
                time.sleep(args.poll_seconds)
    finally:
        driver.close()
        engine.dispose()
    if args.once and ran == 0:
        log.info("queue is empty — nothing to run")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
