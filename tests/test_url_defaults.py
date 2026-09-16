"""Regression tests for #241: Url.is_active defaults to True.

Before the fix, ``Url.is_active`` had no column default, so any ORM-level
``Url.create`` without ``is_active`` raised IntegrityError. The default also
matches the standalone consumer copy (consumer/url_create_handler.py), which
already declared ``default=True``.
"""

from app.models.url import Url


def test_is_active_field_default_is_true():
    """Pre-save: the model field declares default=True."""
    assert Url.is_active.default is True


def test_url_instance_defaults_to_active_without_save(sample_user):
    """Pre-save: omitting is_active yields an active instance."""
    url = Url(
        user=sample_user,
        short_code="deflt-pre1",
        original_url="https://example.com/pre",
        title="Pre-save default",
    )
    assert url.is_active is True


def test_url_create_without_is_active_succeeds_and_reads_back_true(app, sample_user):
    """DB-backed: Url.create without is_active persists and refetches as True."""
    with app.app_context():
        url = Url.create(
            user=sample_user,
            short_code="deflt-db01",
            original_url="https://example.com/default",
            title="Default active",
        )
        assert url.is_active is True

        refetched = Url.get_by_id(url.id)
        assert refetched.is_active is True
