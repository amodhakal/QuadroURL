import io
import os

import pytest

os.environ.setdefault("KAFKA_SYNC_FALLBACK", "1")

from app import create_app
from app.database import db, models
from app.models.user import User
from app.models.url import Url
from migrations_runner import upgrade


@pytest.fixture(scope="session")
def app():
    """Create a Flask app and set up tables once for the entire test session."""
    app = create_app()
    app.config["TESTING"] = True

    with app.app_context():
        # Use the production migrations so new tables cannot be omitted here.
        upgrade(db.obj, models)

    yield app


@pytest.fixture(autouse=True)
def clean_tables(app):
    """Wipe all rows before each test so tests are isolated."""
    from app import _kafka_check_cache, cache
    from app.utils.events import flush_events

    flush_events()
    cache._l1.clear()
    # Reset the cached /ready Kafka probe so one test's result can't leak
    # into the next within the 10s TTL.
    _kafka_check_cache["result"] = None
    _kafka_check_cache["at"] = 0.0
    with app.app_context():
        # Delete dependants first, including tables used by sync event delivery.
        for table in (
            "delivery",
            "subscription",
            "replay",
            "receipt",
            "deadletter",
            "linkmetadata",
            "requestlog",
        ):
            db.execute_sql(f'DELETE FROM "{table}"')
        db.execute_sql("DELETE FROM apikey")
        db.execute_sql("DELETE FROM event")
        db.execute_sql("DELETE FROM url")
        db.execute_sql('DELETE FROM "user"')
    yield


@pytest.fixture()
def seed_auth(app):
    """Seed user + bearer key for the auto-authenticated test client (#99).

    Runs after ``clean_tables`` (autouse fixtures execute first), so the
    seed row is fresh for every test.
    """
    from types import SimpleNamespace

    from app.utils.auth import issue_api_key

    with app.app_context():
        user = User.create(username="authseed", email="authseed@example.com")
        _, raw = issue_api_key(user.id)
        return SimpleNamespace(user=user, api_key=raw)


@pytest.fixture()
def client(app, seed_auth):
    """A Flask test client sending the seed bearer key by default (#99).

    Per-request ``headers``/``environ_overrides`` still win over the default,
    so tests can present other keys (or none) by passing headers explicitly.
    """
    test_client = app.test_client()
    test_client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {seed_auth.api_key}"
    return test_client


@pytest.fixture()
def admin_client(app, client, seed_auth, monkeypatch):
    """Explicit administrator client for cross-user and operator route tests.

    ``auth.is_admin`` resolves ADMIN_USER_IDS from app config (falling back to
    the environment), so pinning config here scopes admin rights to this test
    instead of leaking them into sibling sessions via the process env.
    """
    monkeypatch.setitem(app.config, "ADMIN_USER_IDS", str(seed_auth.user.id))
    return client


@pytest.fixture()
def sample_user(app):
    """Insert and return a single user for tests that need one."""
    with app.app_context():
        user = User.create(username="testuser", email="test@example.com")
        return user


@pytest.fixture()
def owner_client(app, sample_user):
    """Ordinary client authenticated as the owner of sample_user/sample_url."""
    from app.utils.auth import issue_api_key

    with app.app_context():
        _, raw = issue_api_key(sample_user.id)
    test_client = app.test_client()
    test_client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {raw}"
    return test_client


@pytest.fixture()
def sample_url(app, sample_user):
    """Insert and return a URL tied to sample_user."""
    with app.app_context():
        url = Url.create(
            user=sample_user,
            short_code="abc123",
            original_url="https://example.com",
            title="Example",
            is_active=True,
        )
        return url


@pytest.fixture()
def users_csv():
    """Return a file-like CSV payload for bulk import."""
    csv_content = (
        "username,email,created_at\n"
        "alice,alice@example.com,2025-01-01T00:00:00\n"
        "bob,bob@example.com,2025-02-01T00:00:00\n"
    )
    return (io.BytesIO(csv_content.encode("utf-8")), "users.csv")
