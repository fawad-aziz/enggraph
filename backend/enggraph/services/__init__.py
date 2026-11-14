"""Domain services shared by the REST API, the web portal and the MCP server.

Every service is organization-scoped: the organization comes from the
authenticated principal, never from a client-supplied parameter. Traversal
inputs are validated and clamped to configured bounds before they reach the
graph repository.
"""
