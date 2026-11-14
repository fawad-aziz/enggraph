"""Auth API tests: login, session lifecycle, role enforcement, provisioning.

Checks the authorization surface: 401 for missing/forged/expired sessions, one
generic login failure (no account enumeration), admin-only user provisioning,
and that no credential material is returned or stored in the clear.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enggraph.auth.service import create_user
from enggraph.auth.tokens import mint_token, token_hash
from enggraph.main import create_app
from enggraph.persistence.models import AuthSession, Base, User

ADMIN_EMAIL = "admin@acme.test"
VIEWER_EMAIL = "viewer@acme.test"
PASSWORD = "horse-battery-9"


@pytest.fixture()
def app(settings, engine, fake_driver):
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    create_user(
        session,
        email=ADMIN_EMAIL,
        password=PASSWORD,
        role="admin",
        organization_id="acme",
        actor="system",
    )
    create_user(
        session,
        email=VIEWER_EMAIL,
        password=PASSWORD,
        role="viewer",
        organization_id="acme",
    )
    session.close()
    return create_app(settings=settings, engine=engine, driver=fake_driver)


@pytest.fixture()
def client(app):
    # raise_server_exceptions=False: the 500 handler is part of the contract.
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.json()
    return resp.json()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_login_returns_token_and_the_principal_the_api_will_use(client):
    body = login(client, ADMIN_EMAIL)
    assert body["role"] == "admin"
    assert body["organizationId"] == "acme"
    me = client.get("/api/v1/auth/me", headers=bearer(body["token"]))
    assert me.status_code == 200
    assert me.json()["userId"] == body["userId"]
    assert me.json()["sessionId"] == body["sessionId"]


def test_missing_token_is_401_with_the_error_envelope(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "missing_token"
    assert resp.json()["error"]["correlationId"]


def test_non_bearer_scheme_is_rejected(client):
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Basic abc"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "missing_token"


def test_forged_token_is_rejected_with_the_generic_message(client):
    forged = mint_token("a-different-signing-secret")
    resp = client.get("/api/v1/auth/me", headers=bearer(forged))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_expired_session_is_rejected_with_the_same_message(client, engine, settings):
    body = login(client, ADMIN_EMAIL)
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    row = session.execute(
        select(AuthSession).where(AuthSession.token_hash == token_hash(body["token"]))
    ).scalar_one()
    row.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    session.commit()
    session.close()
    resp = client.get("/api/v1/auth/me", headers=bearer(body["token"]))
    assert resp.status_code == 401
    # Same message as a forged token: the response cannot be used to tell an
    # expired session from a session that never existed.
    assert resp.json()["error"]["message"] == "session is invalid or expired"
    assert resp.json()["error"]["code"] == "invalid_token"


def test_login_failure_does_not_disclose_whether_the_account_exists(client):
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "ghost@acme.test", "password": PASSWORD}
    )
    wrong = client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"}
    )
    assert unknown.status_code == 401
    assert wrong.status_code == 401

    def code_message(payload: dict) -> tuple:
        # correlationId is per-request, so it is excluded from the comparison.
        error = payload["error"]
        return (error["code"], error["message"])

    # Identical code and message: no oracle telling "user exists, wrong password".
    assert code_message(unknown.json()) == code_message(wrong.json())
    assert code_message(unknown.json()) == ("invalid_credentials", "invalid email or password")


def test_short_password_is_rejected_before_any_hashing(client):
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "short"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_logout_revokes_the_session(client):
    body = login(client, ADMIN_EMAIL)
    headers = bearer(body["token"])
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    resp = client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 401


def test_user_provisioning_is_admin_only(client):
    viewer = login(client, VIEWER_EMAIL)
    resp = client.post(
        "/api/v1/auth/users",
        headers=bearer(viewer["token"]),
        json={"email": "new@acme.test", "password": PASSWORD, "role": "admin"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "admin_required"


def test_created_users_are_forced_into_the_admins_organization(client):
    admin = login(client, ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/auth/users",
        headers=bearer(admin["token"]),
        json={"email": "attacker@evil.test", "password": PASSWORD, "role": "admin"},
    )
    assert resp.status_code == 201
    assert resp.json()["organizationId"] == "acme"
    created = login(client, "attacker@evil.test")
    assert created["organizationId"] == "acme"
    assert created["role"] == "admin"


def test_role_is_validated(client):
    admin = login(client, ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/auth/users",
        headers=bearer(admin["token"]),
        json={"email": "superuser@acme.test", "password": PASSWORD, "role": "owner"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_role"


def test_no_credential_material_is_stored_in_the_clear(client, engine, settings):
    body = login(client, ADMIN_EMAIL)
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    user = session.execute(select(User).where(User.email == ADMIN_EMAIL)).scalar_one()
    row = session.execute(
        select(AuthSession).where(AuthSession.token_hash == token_hash(body["token"]))
    ).scalar_one()
    assert user.password_hash.startswith("pbkdf2_sha256$")
    assert PASSWORD not in user.password_hash
    # The raw token is never persisted, only its SHA-256 digest.
    assert body["token"] not in row.token_hash
    assert len(row.token_hash) == 64
    session.close()


def test_login_records_an_audit_entry_for_the_users_organization(client, engine):
    login(client, ADMIN_EMAIL)
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    from enggraph.persistence.models import AuditRecord

    rows = (
        session.execute(select(AuditRecord).where(AuditRecord.action == "auth.login"))
        .scalars()
        .all()
    )
    assert rows[0].organization_id == "acme"
    assert "expires_at" in rows[0].details
    session.close()
