"""Entry point: `python -m enggraph.install_schema` — install graph schema.

Run at container startup (after Neo4j is healthy). Repeatable.
"""

from __future__ import annotations

from enggraph.config import load_settings
from enggraph.graph.driver import create_driver
from enggraph.graph.schema import install_schema
from enggraph.logging_setup import get_logger, setup_logging

log = get_logger("enggraph.install_schema")


def main() -> None:
    setup_logging()
    settings = load_settings()
    driver = create_driver(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    try:
        driver.verify_connectivity()
        install_schema(driver)
        log.info("graph schema installed")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
