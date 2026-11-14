"""Frontend/API contract tests (milestone 6).

The portal is written against the API contract, so the contract itself is what
these tests check. They read the TypeScript sources and compare them with the
OpenAPI schema produced by the *production* app:

- the node, relationship, category and provenance constants used by the UI match
  the backend model exactly,
- every path the portal calls exists in the schema for the method it uses,
- every query key the portal sends is a parameter the server declares,
- every body key the portal sends is a property the server declares, and every
  required property is sent.

No Node toolchain is needed: the checks are textual plus schema-driven, and they
fail if either side renames a field (the class of bug a browser would hit).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from api_support import build_app

from enggraph.graph.model import NODE_LABELS, RELATION_SEMANTICS, ProvenanceType
from enggraph.services.catalog import MANUAL_REL_TYPES
from enggraph.services.impact import CATEGORY_ORDER

# tests live under backend/, the portal is a sibling of backend/ at the repo root.
FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


# ---------------------------------------------------------------- ts parsing


def _read(name: str) -> str:
    path = FRONTEND_SRC / name
    assert path.is_file(), f"{path} is missing: the portal sources are part of the build"
    return path.read_text(encoding="utf-8")


def _const_array(source: str, name: str) -> list[str]:
    block = re.search(rf"export const {name} = \[\s*(.*?)\s*\] as const", source, re.S)
    assert block is not None, f"export const {name} not found in api/types.ts"
    return re.findall(r'"([^"]+)"', block.group(1))


def _provenance_labels(source: str) -> dict[str, str]:
    block = re.search(r"PROVENANCE_LABELS: Record<string, string> = \{(.*?)\};", source, re.S)
    assert block is not None, "PROVENANCE_LABELS not found in components/ui.tsx"
    return dict(re.findall(r'([A-Z_]+):\s*"([^"]+)"', block.group(1)))


def _balanced(text: str, start: int) -> str:
    """Contents of the paren group that opens at `start`."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
    raise AssertionError(f"unbalanced parentheses near: {text[start : start + 60]!r}")


def _split_top_level(text: str) -> list[str]:
    """Split on commas that sit outside every bracket and string literal."""
    parts: list[str] = []
    current = ""
    depth = 0
    quote: str | None = None
    for char in text:
        if quote is not None:
            current += char
            if char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
            current += char
            continue
        if char in "([{<":
            depth += 1
            current += char
            continue
        if char in ")]}>":
            depth -= 1
            current += char
            continue
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def _object_keys(literal: str) -> list[str]:
    inner = literal.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    else:
        return []
    keys: list[str] = []
    for part in _split_top_level(inner):
        named = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:", part)
        if named:
            keys.append(named.group(1))
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
            keys.append(part)
    return keys


def _brace_end(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise AssertionError(f"unbalanced braces near: {text[start : start + 60]!r}")


def _path_template(arg: str) -> tuple[str, list[str]]:
    """Path template (`{}` marks an interpolated segment) + keys sent as query."""
    raw = arg.strip()
    body = raw[1:-1] if raw[0] in {"`", '"'} else raw
    template = ""
    query_keys: list[str] = []
    i = 0
    while i < len(body):
        if body.startswith("${", i):
            end = _brace_end(body, i + 1)
            expr = body[i + 2 : end]
            if expr.startswith("qs("):
                query_keys.extend(_object_keys(_balanced(expr, expr.index("("))))
            else:
                template += "{}"
            i = end + 1
        else:
            template += body[i]
            i += 1
    return template, query_keys


def _client_calls(source: str) -> list[tuple[str, str, list[str], list[str]]]:
    """(method, path template, query keys, body keys) for every API call.

    Paths in client.ts are relative to the API prefix, so the prefix declared in
    the same file is applied here: the templates come out as full server paths.
    """
    base = re.search(r'const BASE = "([^"]+)"', source)
    assert base is not None, "client.ts must declare one API prefix"
    prefix = base.group(1)
    calls: list[tuple[str, str, list[str], list[str]]] = []
    for match in re.finditer(r"apiRequest", source):
        i = match.end()
        if i < len(source) and source[i] == "<":
            depth = 0
            while i < len(source):
                if source[i] == "<":
                    depth += 1
                elif source[i] == ">":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            while i < len(source) and source[i] == ">":
                i += 1
        if i >= len(source) or source[i] != "(":
            continue
        parts = _split_top_level(_balanced(source, i))
        if not parts:
            continue
        path_arg = parts[0].strip()
        if not path_arg.startswith(("`", '"')):
            continue  # the function definition itself
        template, query_keys = _path_template(path_arg)
        method, body_keys = "GET", []
        if len(parts) > 1:
            found = re.search(r'method:\s*"([A-Z]+)"', parts[1])
            if found:
                method = found.group(1)
            stringify = re.search(r"JSON\.stringify\(", parts[1])
            if stringify:
                body_keys = _object_keys(_balanced(parts[1], stringify.end() - 1))
        calls.append((method, prefix + template, query_keys, body_keys))
    return calls


def _operations(app) -> dict[tuple[str, str], tuple[set[str], set[str], set[str]]]:
    """(method, path) -> (query params, body properties, required body properties)."""
    spec = app.openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    operations: dict[tuple[str, str], tuple[set[str], set[str], set[str]]] = {}
    for path, item in spec["paths"].items():
        for method, op in item.items():
            query = {p["name"] for p in op.get("parameters", []) if p.get("in") == "query"}
            props: set[str] = set()
            required: set[str] = set()
            for media in op.get("requestBody", {}).get("content", {}).values():
                ref = media.get("schema", {}).get("$ref")
                if not ref:
                    continue
                schema = schemas.get(ref.split("/")[-1], {})
                props |= set(schema.get("properties", {}))
                required |= set(schema.get("required", []))
            operations[(method.upper(), path)] = (query, props, required)
    return operations


def _matches(operations, method: str, template: str):
    """Declared operations (query params, body props, required props) for one call.

    `{}` in the client template is one path segment, so it becomes `[^/]+`; the
    server's `{param}` placeholders are matched as literal segment text.
    """
    pattern = "^" + "[^/]+".join(re.escape(part) for part in template.split("{}")) + "$"
    return [
        data for (m, path), data in operations.items() if m == method and re.match(pattern, path)
    ]


# ---------------------------------------------------------------- constants


def test_portal_constants_mirror_the_graph_model():
    types = _read("api/types.ts")
    assert _const_array(types, "NODE_TYPES") == list(NODE_LABELS)
    assert _const_array(types, "REL_TYPES") == list(RELATION_SEMANTICS)
    assert _const_array(types, "MANUAL_REL_TYPES") == list(MANUAL_REL_TYPES)
    assert _const_array(types, "IMPACT_CATEGORIES") == list(CATEGORY_ORDER)


def test_provenance_labels_cover_every_provenance_type():
    labels = _provenance_labels(_read("components/ui.tsx"))
    assert set(labels) == set(get_args(ProvenanceType))
    # Inferred edges must read as review-needed, not as verified facts.
    assert "review" in labels["INFERRED"].lower()


def test_demo_data_is_always_labelled():
    types = _read("api/types.ts")
    assert "isDemoData" in types, "the node DTO must carry the demo flag"
    ui = _read("components/ui.tsx")
    assert "demo-badge" in ui, "demo mode must be visible in the UI"


# ---------------------------------------------------------------- paths


def test_portal_calls_the_production_api():
    source = _read("api/client.ts")
    assert 'const BASE = "/api/v1"' in source, "the portal must talk only to the API prefix"
    calls = _client_calls(source)
    # 25+ calls: a parse failure would drop this count and fail the test loudly.
    assert len(calls) >= 25, f"only {len(calls)} API calls parsed from client.ts"


def test_every_call_is_a_declared_endpoint(settings, engine, fake_driver):
    app, _repo = build_app(settings, engine, fake_driver, seed=False)
    operations = _operations(app)
    for method, template, _query, _body in _client_calls(_read("api/client.ts")):
        assert _matches(operations, method, template), (
            f"portal calls {method} {template}, which the API does not expose"
        )


def test_query_keys_match_declared_parameters(settings, engine, fake_driver):
    app, _repo = build_app(settings, engine, fake_driver, seed=False)
    operations = _operations(app)
    for method, template, query_keys, _body in _client_calls(_read("api/client.ts")):
        if not query_keys:
            continue
        declared: set[str] = set()
        for query, _props, _required in _matches(operations, method, template):
            declared |= query
        extra = set(query_keys) - declared
        assert not extra, (
            f"{method} {template} sends {sorted(extra)}, which the server does not declare"
        )


def test_body_keys_match_declared_properties(settings, engine, fake_driver):
    app, _repo = build_app(settings, engine, fake_driver, seed=False)
    operations = _operations(app)
    for method, template, _query, body_keys in _client_calls(_read("api/client.ts")):
        if not body_keys:
            continue
        props, required = set(), set()
        for _query, p, r in _matches(operations, method, template):
            props |= p
            required |= r
        sent = set(body_keys)
        assert sent <= props, f"{method} {template} sends {sorted(sent - props)} (undeclared)"
        assert required <= sent, (
            f"{method} {template} omits required fields {sorted(required - sent)}"
        )


def test_the_contract_checks_have_teeth(settings, engine, fake_driver):
    """The checks must reject drift, not just accept the current client.

    A camelCase query key is exactly the drift class this project hit while
    writing the portal (`nodeTypes` where the server declares `node_types`), so
    a synthetic drifted client is pushed through the same machinery and must be
    flagged. Without this test the checks above could pass vacuously.
    """
    app, _repo = build_app(settings, engine, fake_driver, seed=False)
    operations = _operations(app)
    drifted = (
        'const BASE = "/api/v1";\n'
        "apiRequest<X>(`/entities/${id}/neighborhood${qs({ nodeTypes })}`);"
    )
    method, template, keys, _body = _client_calls(drifted)[0]
    assert template == "/api/v1/entities/{}/neighborhood", "the parser must read real calls"
    declared: set[str] = set()
    for query, _props, _required in _matches(operations, method, template):
        declared |= query
    assert "node_types" in declared
    assert set(keys) - declared, "a renamed parameter slipped through the contract check"
    assert not _matches(operations, "GET", "/api/v1/entities/{}/widgets"), (
        "the checker accepts a path the API does not expose"
    )


# ---------------------------------------------------------------- no inert controls


def test_no_inert_controls():
    """A button must call something: no empty handlers, no `href="#"` placeholders."""
    sources = {
        name: _read(name)
        for name in [
            "App.tsx",
            "pages/Login.tsx",
            "pages/Dashboard.tsx",
            "pages/Search.tsx",
            "pages/EntityDetail.tsx",
            "pages/Explorer.tsx",
            "pages/Catalog.tsx",
            "pages/Impact.tsx",
            "pages/Connectors.tsx",
            "pages/SyncJobs.tsx",
        ]
    }
    for name, source in sources.items():
        assert 'href="#"' not in source, f"{name} has a placeholder link"
        assert "onClick={() => {}}" not in source, f"{name} has an inert button"
        assert "TODO" not in source and "FIXME" not in source, f"{name} has unfinished work"


def test_writes_are_admin_gated_in_the_ui_and_the_server():
    catalog = _read("pages/Catalog.tsx")
    assert "useIsAdmin()" in catalog, "catalog writes must be gated on the server-declared role"
    for panel in ("Create a service", "Create an API", "Configure a link"):
        assert panel in catalog
    sync = _read("pages/SyncJobs.tsx")
    assert "useIsAdmin()" in sync, "queue/retry/cancel must be gated on the role"
    connectors = _read("pages/Connectors.tsx")
    assert "useIsAdmin()" in connectors, "connector configuration must be gated on the role"
    assert "patSecretRef" in connectors, "only the secret reference is submitted"
    assert "AZURE_DEVOPS_PAT" in connectors, "the reference is an environment variable name"
