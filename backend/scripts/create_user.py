"""Create a portal user: `python scripts/create_user.py --email ... --role admin --org demo`

There is no `--password` flag: a password in argv lands in shell history and in
the process list. The password is read with `getpass`, written to Postgres as a
PBKDF2 hash by `enggraph.auth.service.create_user`, and never printed.

    --email   login email (unique)
    --role    admin | viewer
    --org     organization the user belongs to
    --min     minimum password length (default 12)
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from enggraph.auth.service import create_user
from enggraph.config import load_settings
from enggraph.logging_setup import setup_logging
from enggraph.persistence.engine import build_engine, build_sessionmaker
from enggraph.persistence.models import UserRoles


def create(session: Session, email: str, role: str, org: str, password: str) -> dict[str, str]:
    """Write the user, return a summary that omits the password."""
    user = create_user(
        session,
        email=email,
        password=password,
        role=role,
        organization_id=org,
        actor="create_user_script",
    )
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "organizationId": user.organization_id,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a portal user. The password is prompted, never passed on the command line."
        ),
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", required=True, choices=UserRoles)
    parser.add_argument("--org", required=True)
    parser.add_argument("--min", dest="min_length", type=int, default=12)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    engine: Engine | None = None,
    read_password: Callable[[str], str] = getpass.getpass,
) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    eng = engine if engine is not None else build_engine(load_settings().postgres_dsn)
    maker = build_sessionmaker(eng)
    session = maker()
    password = read_password("Password: ")
    if len(password) < args.min_length:
        print(f"password must be at least {args.min_length} characters", file=sys.stderr)
        session.close()
        return 2
    try:
        summary = create(session, args.email, args.role, args.org, password)
    except ValueError as refused:
        print(f"refused: {refused}", file=sys.stderr)
        session.close()
        return 2
    print(
        f"user {summary['email']} ({summary['role']}) in organization {summary['organizationId']}"
    )
    print("the password is stored only as a PBKDF2 hash; nothing was echoed.")
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
