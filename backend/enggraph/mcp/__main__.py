"""`python -m enggraph.mcp` — the command a deployment starts.

`docker-compose.yml` runs `python -m enggraph.mcp http --host 0.0.0.0 --port 8100`,
and a stdio client spawns `python -m enggraph.mcp stdio`. Running a package with
`-m` requires this module; the argument parsing and exit codes live in `server.main`.
"""

import sys

from enggraph.mcp.server import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
