"""Secret resolution.

Connector configuration stores a *reference* (an environment variable name).
The secret value itself is never stored in Postgres, never returned by an API
endpoint, and never logged.
"""

from __future__ import annotations

import os

from enggraph.connectors.errors import ConnectorConfigError


def resolve_secret(ref: str) -> str:
    """Resolve a secret reference (environment variable name) to its value.

    Callers must not log the returned value. Missing/blank secrets are a
    configuration error, raised as a typed connector error so the job fails
    with a readable message that contains no secret material.
    """
    if not ref or not ref.replace("_", "").isalnum():
        raise ConnectorConfigError(f"invalid secret reference: {ref!r}")
    value = os.environ.get(ref, "")
    if not value:
        raise ConnectorConfigError(f"secret reference {ref!r} is not set in the environment")
    return value
