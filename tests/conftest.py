import io
import os

import pytest

os.environ.setdefault("KAFKA_SYNC_FALLBACK", "1")

from app import create_app
from app.database import db
from app.models.user import User
from app.models.url import Url
from app.models.event import Event


@pytest.fixture(scope="session")
def app():
    """Create a Flask app and set up tables once for the entire test session."""
    app = create_app()
    app.config["TESTING"] = True

    with app.app_context():
        db.create_tables([User, Url, Event], safe=True)

    yield app


@pytest.fixture(autouse=True)
def clean_tables(app):
    """Wipe all rows before each test so tests are isolated."""
    from app.utils.events import flush_events
    flush_events()
    with app.app_context():
        db.execute_sql("DELETE FROM event")
        db.execute_sql("DELETE FROM url")
        db.execute_sql("DELETE FROM \"user\"")
    yield


@pytest.fixture()
def client(app):
    """A Flask test client for sending HTTP requests."""
    return app.test_client()


@pytest.fixture()
def sample_user(app):
    """Insert and return a single user for tests that need one."""
    with app.app_context():
        user = User.create(username="testuser", email="test@example.com")
        return user


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
