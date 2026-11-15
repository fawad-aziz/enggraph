"""Remove demo data (spec §8): `python scripts/demo_reset.py`.

The reset is scoped to one organization and to demo-marked nodes only
(`demoData=true`), so it cannot remove provider-ingested or manually cataloged
records. The organization's configuration row is left in place: after a reset
the organization is still visible with zero counts.

    --org demo   organization whose demo records should be removed
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session

from enggraph.config import Settings, load_settings, validate_runtime
from enggraph.demo import DEMO_ACTOR, DEMO_ORG, reset_demo
from enggraph.graph.driver import open_driver
from enggraph.graph.neo4j_repo import Neo4jGraphRepository
from enggraph.graph.repository import GraphRepository
from enggraph.logging_setup import setup_logging
from enggraph.persistence.engine import build_engine, build_sessionmaker


def run(repo: GraphRepository, session: Session, org: str) -> dict[str, object]:
    """The reset itself, against the given stores (no settings, no network)."""
    return reset_demo(repo, session, org=org, actor=DEMO_ACTOR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Remove demo-marked records from one organization (provider data is untouched)."
        ),
    )
    parser.add_argument("--org", default=DEMO_ORG, help="organization whose demo records to remove")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings: Settings = load_settings()
    problems = validate_runtime(settings)
    if problems:
        for problem in problems:
            print(f"config: {problem}", file=sys.stderr)
        return 2
    setup_logging()
    engine = build_engine(settings.postgres_dsn)
    maker = build_sessionmaker(engine)
    driver = open_driver(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    if driver is None:
        print(
            f"Neo4j is unreachable at {settings.neo4j_uri}; start the graph store first",
            file=sys.stderr,
        )
        return 2
    repo = Neo4jGraphRepository(driver)
    session = maker()
    try:
        result = run(repo, session, args.org)
    finally:
        session.close()
        driver.close()
    print(f"removed {result['nodesRemoved']} demo nodes from organization {args.org!r}")
    print("remaining counts by label:", result["counts"])
    print("provider and manual catalog records were not touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
