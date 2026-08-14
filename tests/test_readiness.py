"""Tests for the /ready endpoint, async URL-create contract, and producer
backpressure handling."""

import redis


class FakeRedis:
    def ping(self):
        return True


class FakeProducer:
    def __init__(self, fail_first_n=0):
        self.calls = 0
        self.fail_first_n = fail_first_n

    def produce(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_first_n:
            raise BufferError("local queue is full")

    def poll(self, timeout=0):
        return None

    def list_topics(self, timeout=None):
        return {}


# ---------------------------------------------------------------------------
# /ready
# ---------------------------------------------------------------------------

def test_ready_returns_ok_when_dependencies_up(client, monkeypatch):
    import app.cache as cache
    import app.utils.kafka_producer as kp

    monkeypatch.setattr(cache, "get_l2", lambda: FakeRedis())
    monkeypatch.setattr(kp, "get_producer", lambda: FakeProducer())

    response = client.get("/ready")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["checks"]["postgres"] == "ok"
    assert data["checks"]["redis"] == "ok"
    assert data["checks"]["kafka"] == "ok"


def test_ready_returns_503_when_postgres_down(client, monkeypatch):
    import app.cache as cache
    import app.database as database
    import app.utils.kafka_producer as kp

    monkeypatch.setattr(database.db, "execute_sql", lambda *a, **k: (_ for _ in ()).throw(Exception("db down")))
    monkeypatch.setattr(cache, "get_l2", lambda: FakeRedis())
    monkeypatch.setattr(kp, "get_producer", lambda: FakeProducer())

    response = client.get("/ready")
    assert response.status_code == 503
    data = response.get_json()
    assert data["status"] == "not_ready"
    assert data["checks"]["postgres"] != "ok"


def test_ready_returns_503_when_redis_down(client, monkeypatch):
    import app.cache as cache
    import app.utils.kafka_producer as kp

    monkeypatch.setattr(cache, "get_l2", lambda: None)
    monkeypatch.setattr(kp, "get_producer", lambda: FakeProducer())

    response = client.get("/ready")
    assert response.status_code == 503
    assert response.get_json()["checks"]["redis"] != "ok"


def test_ready_returns_503_when_kafka_down(client, monkeypatch):
    import app.cache as cache
    import app.utils.kafka_producer as kp

    monkeypatch.setattr(cache, "get_l2", lambda: FakeRedis())

    def bad_producer():
        raise kp.ProducerBackpressureError("kafka down")

    monkeypatch.setattr(kp, "get_producer", bad_producer)

    response = client.get("/ready")
    assert response.status_code == 503
    assert response.get_json()["checks"]["kafka"] != "ok"


# ---------------------------------------------------------------------------
# /health stays a pure liveness check
# ---------------------------------------------------------------------------

def test_health_does_not_touch_db(client, monkeypatch):
    import app.database as database

    def boom(*a, **k):
        raise AssertionError("health must not touch the database")

    monkeypatch.setattr(database.db, "execute_sql", boom)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Async two-phase URL create contract
# ---------------------------------------------------------------------------

def test_create_url_returns_202_with_request_id_when_async(client, sample_user, monkeypatch):
    monkeypatch.setenv("KAFKA_SYNC_FALLBACK", "0")

    response = client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/async",
        "title": "Async",
    })
    assert response.status_code == 202
    data = response.get_json()
    assert data["status"] == "pending"
    assert "request_id" in data


def test_url_status_returns_503_when_status_store_down(client, sample_user, monkeypatch):
    monkeypatch.setenv("KAFKA_SYNC_FALLBACK", "0")

    class DownClient:
        def get(self, key):
            raise redis.RedisError("connection refused")

    def fake_from_url(*a, **k):
        return DownClient()

    monkeypatch.setattr(redis, "from_url", fake_from_url)

    response = client.get("/urls/whatever/status")
    assert response.status_code == 503
    assert response.is_json


# ---------------------------------------------------------------------------
# Producer backpressure
# ---------------------------------------------------------------------------

def test_produce_raises_backpressure_error_when_queue_stays_full(monkeypatch):
    from app.utils import kafka_producer as kp

    monkeypatch.setenv("KAFKA_PRODUCE_TIMEOUT", "0.2")
    monkeypatch.setattr(kp, "_get_producer", lambda: FakeProducer(fail_first_n=1000))

    try:
        kp._produce("test-topic", {"a": 1})
    except kp.ProducerBackpressureError:
        return
    raise AssertionError("expected ProducerBackpressureError")


def test_produce_succeeds_after_buffer_error_retry(monkeypatch):
    from app.utils import kafka_producer as kp

    fake = FakeProducer(fail_first_n=2)
    monkeypatch.setattr(kp, "_get_producer", lambda: fake)

    kp._produce("test-topic", {"a": 1})
    assert fake.calls >= 3


def test_sync_fallback_publish_event_writes_to_db(app, sample_url, sample_user, monkeypatch):
    from app.models.event import Event

    monkeypatch.setenv("KAFKA_SYNC_FALLBACK", "1")
    from app.utils.kafka_producer import publish_event

    publish_event({
        "url_id": sample_url.id,
        "user_id": sample_user.id,
        "event_type": "click",
        "details": {"foo": "bar"},
    })

    with app.app_context():
        count = Event.select().where(Event.event_type == "click").count()
    assert count == 1