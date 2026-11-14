"""Versioned REST API (`/api/v1`).

Routers are thin: validation, auth, and response envelope. All domain work is
delegated to `enggraph.services` and the graph repository.
"""
