"""Tests for DELETE /users/<id> cascade (issue #237)."""


def test_delete_user_with_urls_and_events(app, owner_client, sample_user):
    from app.cache import get_url_by_short_code, get_user
    from app.models.event import Event
    from app.models.url import Url
    from app.models.user import User

    with app.app_context():
        url = Url.create(
            user=sample_user,
            short_code="delme1",
            original_url="https://example.com",
            title="Doomed",
            is_active=True,
        )
        Event.create(
            url_id=url.id,
            user_id=sample_user.id,
            event_type="click",
            details="{}",
        )
        user_id = sample_user.id
        url_id = url.id

    # Prime caches so we can assert coherence after delete.
    assert owner_client.get(f"/users/{user_id}").status_code == 200
    assert get_user(user_id) is not None
    assert owner_client.get(f"/urls/{url_id}").status_code == 200
    assert get_url_by_short_code("delme1") is not None

    response = owner_client.delete(f"/users/{user_id}")
    assert response.status_code == 200

    with app.app_context():
        assert User.get_or_none(User.id == user_id) is None
        assert Url.get_or_none(Url.id == url_id) is None
        assert Event.select().where(Event.user == user_id).count() == 0
        assert Event.select().where(Event.url == url_id).count() == 0

    # Caches coherent: user + short-code entries miss after delete.
    assert get_user(user_id) is None
    assert get_url_by_short_code("delme1") is None


def test_delete_user_nonexistent(client):
    response = client.delete("/users/99999")
    # Ownership is checked before existence for ordinary users.
    assert response.status_code == 404
