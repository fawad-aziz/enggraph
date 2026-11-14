"""Docker Compose contract (milestone 7): the deployment names things that exist.

The scaffold's `mcp-server` service ran `python -m enggraph.mcp_server` — a module
that does not exist, so the service would have crashed at startup with nothing to
point at. That is the class of defect pinned here: every module a compose command
runs is checked against the installed package, the scripts a command invokes are
checked on disk, the stores stay loopback-published, and a bind that clients reach
through a published port declares the address they present.

Docker is not available on this machine, so compose is checked as a document —
the services are never started here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"
BACKEND_DIR = COMPOSE_PATH.parent / "backend"
LOCALHOSTS = ("127.0.0.1", "localhost", "::1")


def _services() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())["services"]


def _command_modules(command: list[str]) -> list[str]:
    return [command[index + 1] for index, part in enumerate(command) if part == "-m"]


def test_every_module_a_compose_command_runs_exists():
    """`python -m X` in a compose command must name a module this package provides."""
    missing = []
    for name, service in _services().items():
        command = service.get("command") or []
        if not command or command[0] not in ("python", "python3"):
            continue
        for module in _command_modules(command):
            if importlib.util.find_spec(module) is None:
                missing.append((name, module))
    assert missing == []


def test_every_script_a_compose_command_invokes_is_in_the_image():
    """The backend entrypoint is copied from `backend/`, so the path it runs must
    exist relative to the build context."""
    entrypoint = _services()["backend"]["command"][1]
    # /app is the image WORKDIR; the build context is backend/.
    relative = entrypoint.removeprefix("/app/")
    assert (BACKEND_DIR / relative).is_file(), f"{relative} is not in the build context"


def test_the_graph_stores_and_the_mcp_endpoint_are_loopback_published():
    """Neo4j, Postgres and the MCP endpoint are reachable from the host only, so a
    browser can talk to the backend API and not to the graph stores."""
    published = {
        name: service.get("ports", [])
        for name, service in _services().items()
        if service.get("ports")
    }
    assert published, "the deployment publishes ports"
    for name, mappings in published.items():
        for mapping in mappings:
            assert mapping.startswith("127.0.0.1:"), f"{name} publishes {mapping}"


def test_a_non_loopback_mcp_bind_declares_the_address_its_clients_present():
    """A container publishes a loopback port while the process binds every interface,
    so the DNS-rebinding protection must be told the address clients present."""
    service = _services()["mcp-server"]
    command = service["command"]
    environment = service["environment"]
    bind = command[command.index("--host") + 1]
    port = command[command.index("--port") + 1]
    if bind not in LOCALHOSTS:
        allowed_hosts = environment["MCP_ALLOWED_HOSTS"]
        allowed_origins = environment["MCP_ALLOWED_ORIGINS"]
        assert "127.0.0.1:*" in allowed_hosts
        assert f"http://127.0.0.1:{port}" in allowed_origins
    else:
        # The SDK's own localhost protection is enough; nothing extra to declare.
        assert environment.get("MCP_ALLOWED_HOSTS", "") == ""


def test_the_services_that_read_the_stores_wait_for_healthy_stores():
    """Nothing starts against a store that is not ready, and the data lives in named
    volumes so a restart keeps the graph."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    healthy = {"postgres", "neo4j"}
    for name in ("backend", "worker", "mcp-server"):
        conditions = _services()[name]["depends_on"]
        assert set(conditions) == healthy, f"{name} waits for {sorted(conditions)}"
        assert all(conditions[store]["condition"] == "service_healthy" for store in healthy)
    assert set(compose["volumes"]) == {"neo4j_data", "postgres_data"}
