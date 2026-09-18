"""OpenRouter client contracts — hermetic, no services (HTTP mocked)."""

import pytest

from shared import openrouter
from shared.openrouter import OpenRouterError, chat_complete, embed_texts


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture(autouse=True)
def clean_tables():
    yield  # no integration DB needed


def test_embed_texts_returns_vectors_sorted_by_index(monkeypatch):
    calls = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["payload"] = json
        return _Response(
            200,
            {
                "data": [
                    {"index": 1, "embedding": [0.2, 0.3]},
                    {"index": 0, "embedding": [0.1, 0.4]},
                ],
                "usage": {"prompt_tokens": 12},
            },
        )

    monkeypatch.setattr(openrouter.requests, "post", fake_post)
    vectors, tokens = embed_texts(["a", "b"], model="m", key="k", input_type="search_query")
    assert vectors == [[0.1, 0.4], [0.2, 0.3]]
    assert tokens == 12
    assert calls["payload"]["input_type"] == "search_query"
    assert calls["payload"]["model"] == "m"


def test_embed_texts_posts_to_embeddings_endpoint(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        return _Response(200, {"data": [{"index": 0, "embedding": [1.0]}]})

    monkeypatch.setattr(openrouter.requests, "post", fake_post)
    embed_texts(["x"], model="m", key="k")
    assert seen["url"].endswith("/embeddings")


def test_embed_texts_rate_limit_is_retryable(monkeypatch):
    monkeypatch.setattr(
        openrouter.requests,
        "post",
        lambda *a, **k: _Response(429, {"error": {"message": "slow down"}}),
    )
    with pytest.raises(OpenRouterError) as exc_info:
        embed_texts(["x"], model="m", key="k")
    assert exc_info.value.status == 429
    assert exc_info.value.retryable is True


def test_embed_texts_unreachable_is_retryable(monkeypatch):
    def boom(*a, **k):
        raise openrouter.requests.ConnectionError("down")

    monkeypatch.setattr(openrouter.requests, "post", boom)
    with pytest.raises(OpenRouterError) as exc_info:
        embed_texts(["x"], model="m", key="k")
    assert exc_info.value.retryable is True


def test_embed_texts_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(OpenRouterError):
        embed_texts(["x"], model="m")


def test_chat_complete_falls_back_on_429_then_succeeds(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if json["model"] == "primary":
            return _Response(429, {"error": {"message": "quota"}})
        return _Response(
            200,
            {
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    monkeypatch.setattr(openrouter.requests, "post", fake_post)
    content, prompt, completion, used = chat_complete(
        [{"role": "user", "content": "hi"}],
        model="primary",
        fallback_model="backup",
        key="k",
    )
    assert content == "hello"
    assert (prompt, completion) == (5, 2)
    assert used == "backup"
    assert calls == ["primary", "backup"]


def test_chat_complete_raises_when_all_models_fail(monkeypatch):
    monkeypatch.setattr(
        openrouter.requests,
        "post",
        lambda *a, **k: _Response(500, {"error": {"message": "boom"}}),
    )
    with pytest.raises(OpenRouterError) as exc_info:
        chat_complete([{"role": "user", "content": "hi"}], model="a", fallback_model="b", key="k")
    assert exc_info.value.retryable is True


def test_chat_complete_client_error_does_not_fallback(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        return _Response(400, {"error": {"message": "bad request"}})

    monkeypatch.setattr(openrouter.requests, "post", fake_post)
    with pytest.raises(OpenRouterError) as exc_info:
        chat_complete([{"role": "user", "content": "hi"}], model="a", fallback_model="b", key="k")
    assert exc_info.value.status == 400
    assert calls == ["a"]
