"""Docker Compose contract (milestones 7-8): the deployment names things that exist.

The scaffold's `mcp-server` service ran `python -m enggraph.mcp_server` — a module
that does not exist, so the service would have crashed at startup with nothing to
point at. That is the class of defect pinned here: every module a compose command
runs is checked against the installed package, the scripts a command invokes are
checked on disk, the stores stay loopback-published, and a bind that clients reach
through a published port declares the address they present.

The milestone-8 acceptance items "fresh setup" and "restart persistence" are pinned
as far as this machine can verify them: the first start bootstraps migrations and
graph schema before the API serves, store data lives in named volumes mounted at
the store's own data path, and every app service reaches the stores over the network
so a restarted store is re-read rather than re-created.

Docker is not available on this machine, so compose is checked as a document —
the services are never started here, and the volume/health behaviour is asserted from
the compose file, not from a running container.
"""

from __future__ import annotations

import importlib.util
import re
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


def test_a_fresh_setup_bootstraps_the_stores_before_the_api_serves():
    """First start on an empty machine: migrations and graph schema run before the
    API accepts requests, so the first request sees tables, constraints and indexes."""
    entrypoint = _services()["backend"]["command"][1]
    relative = entrypoint.removeprefix("/app/")
    text = (BACKEND_DIR / relative).read_text()
    assert "alembic upgrade head" in text
    assert "python -m enggraph.install_schema" in text
    # Bootstrap first, serve last: a schema that arrives after uvicorn is a schema
    # the first requests never saw.
    assert text.index("install_schema") < text.index("uvicorn")
    for module in ("enggraph.install_schema", "enggraph.worker", "enggraph.main"):
        assert importlib.util.find_spec(module) is not None, module


def _decoded_setting(env_name: str) -> str:
    """Reproduce the entrypoint's decoding: a single `_` in the encoded name is a
    period in the setting, a doubled `__` is a real underscore. The order matters —
    substitute the doubled form first, so `auth__enabled` becomes `auth_enabled` and
    `dbms_security` becomes `dbms.security`."""
    body = env_name.removeprefix("NEO4J_")
    body = body.replace("__", "\x00")
    body = body.replace("_", ".")
    return body.replace("\x00", "_")


def test_the_neo4j_environment_names_only_settings_the_entrypoint_can_parse():
    """The Neo4j entrypoint reads every `NEO4J_` variable as a config setting and
    refuses to start on a name it cannot map (`Failed to read config: Unrecognized
    setting. No declared setting with name: …`). Two failures of this class happened
    on real container starts: `NEO4J_SERVER__PROPERTIES` (not a setting at all) and
    `NEO4J_dbms_security__auth_enabled`, which decodes to
    `dbms.security_auth.enabled` — the doubled underscore must sit on the setting's
    own underscore (`auth_enabled`), not on the separator before it. Because every
    app service gates on the neo4j healthcheck, one unparseable variable stops the
    whole stack. Pinned as a document: no container is started on this machine.
    """
    environment = _services()["neo4j"]["environment"]
    name_pattern = re.compile(r"^NEO4J_[a-z0-9]+(?:_{1,2}[a-z0-9]+)*$")
    for name in sorted(environment):
        if name == "NEO4J_AUTH":
            continue
        assert name_pattern.match(name), f"{name} is not a name the entrypoint parses"
    # The setting the deployment asserts (Neo4j 5 defaults it to true; stated, not
    # assumed) and the exact encoding the entrypoint must decode to it.
    assert _decoded_setting("NEO4J_SERVER__PROPERTIES") == "SERVER_PROPERTIES"
    assert _decoded_setting("NEO4J_dbms_security__auth_enabled") == "dbms.security_auth.enabled"
    assert _decoded_setting("NEO4J_dbms_security_auth__enabled") == "dbms.security.auth_enabled"
    encoded = next(name for name in environment if name != "NEO4J_AUTH")
    assert _decoded_setting(encoded) == "dbms.security.auth_enabled", (
        f"{encoded} decodes to {_decoded_setting(encoded)}, not a declared setting"
    )
    assert environment[encoded] == "true"


def test_a_restart_reads_the_same_store_data_the_first_start_wrote():
    """Persistence is volume-backed: each store mounts its own named volume at its
    data path, and the app services reach the stores by service name. A restarted
    store is re-read over the network, not rebuilt from a host directory."""
    services = _services()
    mounts = {
        name: service.get("volumes", [])
        for name, service in services.items()
        if service.get("volumes")
    }
    assert mounts == {
        "neo4j": ["neo4j_data:/data"],
        "postgres": ["postgres_data:/var/lib/postgresql/data"],
    }
    for mapping in [m for group in mounts.values() for m in group]:
        assert not mapping.startswith("./"), f"{mapping} is a host bind mount"
    for name in ("backend", "worker", "mcp-server"):
        environment = services[name]["environment"]
        assert environment["NEO4J_URI"] == "bolt://neo4j:7687"
        assert environment["POSTGRES_DSN"].endswith("postgres:5432/enggraph")


def test_the_mcp_command_is_an_entry_point_and_the_published_port_is_the_bind_port():
    """`python -m enggraph.mcp` needs `enggraph/mcp/__main__.py` to run, and the port
    the process binds is the port the deployment publishes: a client pointing at the
    published mapping reaches the process."""
    service = _services()["mcp-server"]
    command = service["command"]
    assert command[:4] == ["python", "-m", "enggraph.mcp", "http"]
    published = service["ports"][0]
    assert published.split(":")[2] == command[command.index("--port") + 1]
    package = importlib.util.find_spec("enggraph.mcp")
    assert package is not None and package.origin is not None
    entry = Path(package.origin).parent / "__main__.py"
    assert entry.is_file(), f"{entry} is missing, so `python -m enggraph.mcp` cannot run"
