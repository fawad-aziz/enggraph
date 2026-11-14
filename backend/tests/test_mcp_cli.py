"""The MCP server's launch path (milestone 7): what the operator/client runs.

`main` is the entry point a stdio client spawns and the command an HTTP deployment
starts. What is pinned here is the process behaviour, not the tool surface (the tool
contract and authorization are exercised in `test_mcp_tools.py` / `test_mcp_http.py`):

- a configuration problem is reported on the process's own output and the process
  exits before any transport is opened, so a half-configured server never serves;
- stdio warns, in advance, that tool calls will be refused without the operator
  token, and stays quiet when the operator supplied one;
- `--host` / `--port` reach the HTTP runner, and the settings' own values reach the
  server the real `run_http` builds, together with the Host patterns the settings
  declare (accepted) and an unrelated Host (refused).

`setup_logging()` rebuilds the root handlers, so these assertions read the process's
real stderr (`capfd`) rather than a pytest log fixture.
"""

from __future__ import annotations

from typing import Any

from enggraph.mcp import server as mcp_server_module
from enggraph.mcp.server import EXIT_CONFIG, EXIT_OK, main

MCP_TOKEN_ENV = "ENGGRAPH_MCP_TOKEN"


def test_a_configuration_problem_stops_the_process_before_anything_is_served(
    settings, monkeypatch, capfd
):
    """Nothing is served on a broken configuration: the problem is named on the
    output the operator sees, the exit code is the configuration code, and no
    transport function is reached."""
    broken = settings.model_copy(update={"neo4j_password": ""})
    calls: list[str] = []
    monkeypatch.setattr(mcp_server_module, "load_settings", lambda: broken)
    monkeypatch.setattr(mcp_server_module, "run_stdio", lambda: calls.append("stdio"))
    monkeypatch.setattr(mcp_server_module, "run_http", lambda host, port: calls.append("http"))
    assert main(["stdio"]) == EXIT_CONFIG
    assert calls == []
    assert "NEO4J_PASSWORD is empty" in capfd.readouterr().err


def test_stdio_serves_and_says_ahead_that_calls_will_be_refused_without_a_token(
    settings, monkeypatch, capfd
):
    """The warning is printed at startup, not discovered per call: an operator with no
    `ENGGRAPH_MCP_TOKEN` learns immediately that every tool call will be refused."""
    calls: list[str] = []
    monkeypatch.delenv(MCP_TOKEN_ENV, raising=False)
    monkeypatch.setattr(mcp_server_module, "load_settings", lambda: settings)
    monkeypatch.setattr(mcp_server_module, "run_stdio", lambda: calls.append("stdio"))
    assert main(["stdio"]) == EXIT_OK
    assert calls == ["stdio"]
    assert MCP_TOKEN_ENV in capfd.readouterr().err


def test_stdio_with_an_operator_token_starts_without_a_warning(settings, monkeypatch, capfd):
    """The warning is about the missing token, not a blanket startup notice."""
    monkeypatch.setenv(MCP_TOKEN_ENV, "a-session-token")
    calls: list[str] = []
    monkeypatch.setattr(mcp_server_module, "load_settings", lambda: settings)
    monkeypatch.setattr(mcp_server_module, "run_stdio", lambda: calls.append("stdio"))
    assert main(["stdio"]) == EXIT_OK
    assert calls == ["stdio"]
    assert MCP_TOKEN_ENV not in capfd.readouterr().err


def test_http_options_reach_the_runner_and_the_defaults_defer_to_settings(settings, monkeypatch):
    """`--host` / `--port` are passed through; without them the runner is given `None`,
    which is the signal to resolve `MCP_HOST` / `MCP_PORT`."""
    captured: list[tuple[str | None, int | None]] = []
    monkeypatch.setattr(
        mcp_server_module,
        "load_settings",
        lambda: settings.model_copy(update={"mcp_host": "10.0.0.5", "mcp_port": 9010}),
    )
    monkeypatch.setattr(
        mcp_server_module, "run_http", lambda host, port: captured.append((host, port))
    )
    assert main(["http"]) == EXIT_OK
    assert captured == [(None, None)]
    assert main(["http", "--host", "127.0.0.1", "--port", "8123"]) == EXIT_OK
    assert captured[-1] == ("127.0.0.1", 8123)


async def test_the_http_launch_serves_the_host_the_settings_declare_and_its_patterns(
    settings, monkeypatch
):
    """The app a real `run_http` builds binds the settings' host/port, accepts the Host
    pattern the settings declare, and refuses an unrelated one: a deployment gets
    exactly what it configured. The server is stateful (the `run_http` default), so a
    client must accept both JSON and the event-stream media type."""
    import uvicorn
    from mcp_support import raw_post, serving

    apps: list[Any] = []
    bound: list[tuple[str, int]] = []

    def fake_run(app: Any, host: str, port: int) -> None:
        apps.append(app)
        bound.append((host, port))

    monkeypatch.setattr(
        mcp_server_module,
        "load_settings",
        lambda: settings.model_copy(
            update={
                "mcp_port": 8123,
                "mcp_allowed_hosts": "127.0.0.1:*",
                "mcp_allowed_origins": "http://127.0.0.1:8123",
            }
        ),
    )
    # Neo4j is not in this environment: the app must start with the store unreachable.
    monkeypatch.setattr(mcp_server_module, "open_driver", lambda uri, user, password: None)
    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert main(["http"]) == EXIT_OK
    assert bound == [("127.0.0.1", 8123)]
    stream_accept = "application/json, text/event-stream"
    async with serving(apps[0]):
        assert (
            await raw_post(apps[0], "http://127.0.0.1:8123", accept=stream_accept)
        ).status_code == 200
        assert (
            await raw_post(apps[0], "http://evil.example:8123", accept=stream_accept)
        ).status_code == 421
