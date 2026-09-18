"""Minimal OpenRouter HTTP client shared by the API and Kafka consumers.

Only the standard library plus ``requests`` are used so the consumer service
(which cannot import ``app``) can embed link content on ingest. All calls are
fail-open by contract: they raise :class:`OpenRouterError` and callers decide
whether to degrade (ingest/search must never break on an LLM outage).

Model slugs are never hardcoded here — they arrive as arguments, resolved by
callers from the environment (``OPENROUTER_CHAT_MODEL``,
``OPENROUTER_FALLBACK_MODEL``, ``OPENROUTER_EMBEDDING_MODEL``).
"""

import logging
import os

import requests

logger = logging.getLogger("quadroPE.openrouter")

BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_CHAT_MODEL = "qwen/qwen3.8-27b:free"
DEFAULT_CHAT_FALLBACK_MODEL = "openrouter/free"
DEFAULT_EMBEDDING_MODEL = "liquid/lfm-2.5-embedding-350m:free"


class OpenRouterError(Exception):
    """An OpenRouter call failed. ``status`` is the HTTP status (if any)."""

    def __init__(self, message, status=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def api_key():
    return (os.environ.get("OPENROUTER_API_KEY") or "").strip()


def require_api_key():
    key = api_key()
    if not key:
        raise OpenRouterError("OPENROUTER_API_KEY is not configured", retryable=False)
    return key


def _headers(key):
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    referer = os.environ.get("OPENROUTER_HTTP_REFERER", "")
    if referer:
        headers["HTTP-Referer"] = referer
    title = os.environ.get("OPENROUTER_APP_TITLE", "QuadroURL")
    if title:
        headers["X-Title"] = title
    return headers


def _request_error(response):
    try:
        detail = response.json()
        message = str(detail.get("error", {}).get("message", detail))[:300]
    except Exception:
        message = response.text[:300]
    retryable = response.status_code == 429 or response.status_code >= 500
    return OpenRouterError(
        f"OpenRouter {response.status_code}: {message}",
        status=response.status_code,
        retryable=retryable,
    )


def embed_texts(texts, *, model, key=None, input_type="search_document", timeout=30):
    """Embed ``texts`` with an OpenRouter embedding model.

    ``input_type`` follows the embeddings API vocabulary: ingest stores
    passages as ``search_document`` while query-time retrieval uses
    ``search_query`` (asymmetric prompt for the bi-encoder).
    Returns ``(vectors, prompt_tokens)``.
    """
    key = key or require_api_key()
    payload = {"model": model, "input": list(texts), "encoding_format": "float"}
    if input_type:
        payload["input_type"] = input_type
    try:
        response = requests.post(
            f"{BASE_URL}/embeddings",
            headers=_headers(key),
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise OpenRouterError(f"OpenRouter embeddings unreachable: {exc}", retryable=True)
    if response.status_code != 200:
        raise _request_error(response)
    try:
        body = response.json()
        vectors = [item["embedding"] for item in sorted(body["data"], key=lambda d: d["index"])]
        prompt_tokens = int(((body.get("usage") or {}).get("prompt_tokens")) or 0)
    except (KeyError, TypeError, ValueError) as exc:
        raise OpenRouterError(f"OpenRouter embeddings bad payload: {exc}", retryable=False)
    return vectors, prompt_tokens


def chat_complete(messages, *, model, fallback_model=None, key=None, timeout=60, max_tokens=512):
    """Chat completion with one fallback model hop on 404/429/5xx.

    Returns ``(content, prompt_tokens, completion_tokens, used_model)``.
    """
    key = key or require_api_key()
    candidates = [model] + ([fallback_model] if fallback_model and fallback_model != model else [])
    last_error = None
    for candidate in candidates:
        try:
            response = requests.post(
                f"{BASE_URL}/chat/completions",
                headers=_headers(key),
                json={
                    "model": candidate,
                    "messages": messages,
                    "max_tokens": max_tokens,
                },
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = OpenRouterError(f"OpenRouter chat unreachable: {exc}", retryable=True)
            continue
        if response.status_code != 200:
            last_error = _request_error(response)
            if response.status_code in (404, 429) or response.status_code >= 500:
                logger.warning(f"Chat model {candidate} failed, trying fallback: {last_error}")
                continue
            raise last_error
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"] or ""
            usage = body.get("usage") or {}
            return (
                content,
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                candidate,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise OpenRouterError(f"OpenRouter chat bad payload: {exc}", retryable=False)
    raise last_error
