"""Load the labeled demo dataset (spec §8): `python scripts/demo_load.py`.

Demo data is written through the same graph repository the sync worker uses, so
this script needs the running stores (Neo4j + Postgres) exactly like the API.
Every record it writes is marked `demoData=true` and `sourceSystem="demo"`, and
the loader refuses to write into an organization that already holds provider or
manual catalog records.

    --org demo     organization id to load into (default: demo)
    --replace      remove that organization's demo records first, then load

`--replace` is the safe reload: it removes only demo nodes, never provider data.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session

from enggraph.config import Settings, load_settings, validate_runtime
from enggraph.demo import DEMO_ACTOR, DEMO_ORG, DemoConflictError, load_demo, reset_demo
from enggraph.graph.driver import open_driver
from enggraph.graph.neo4j_repo import Neo4jGraphRepository
from enggraph.graph.repository import GraphRepository
from enggraph.logging_setup import setup_logging
from enggraph.persistence.engine import build_engine, build_sessionmaker


def run(repo: GraphRepository, session: Session, org: str, replace: bool) -> dict[str, object]:
    """The load itself, against the given stores (no settings, no network)."""
    if replace:
        reset_demo(repo, session, org=org, actor=DEMO_ACTOR)
    return load_demo(repo, session, org=org, actor=DEMO_ACTOR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load the labeled EngGraph demo dataset (synthetic, clearly marked).",
    )
    parser.add_argument("--org", default=DEMO_ORG, help="organization id to load into")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="remove this organization's demo records before loading",
    )
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
        # Loud failure, not a silent partial load: demo data goes to the graph store.
        print(
            f"Neo4j is unreachable at {settings.neo4j_uri}; start the graph store first",
            file=sys.stderr,
        )
        return 2
    repo = Neo4jGraphRepository(driver)
    session = maker()
    try:
        result = run(repo, session, args.org, args.replace)
    except DemoConflictError as conflict:
        print(f"refused: {conflict}", file=sys.stderr)
        return 2
    finally:
        session.close()
        driver.close()
    print(f"demo data loaded into organization {args.org!r} (demoData=true)")
    print(f"nodes: {result['nodesWritten']}, edges: {result['edgesWritten']}")
    print("counts by label:", result["counts"])
    print("labels are demo fixtures: they are not a real organization's data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
