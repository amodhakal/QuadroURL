"""Semantic search and RAG Q&A over saved links.

``GET /search`` embeds the query (Redis-cached) and returns the top-k most
similar active links via pgvector cosine similarity. ``POST /ask`` runs the
same retrieval, then synthesizes an answer with an OpenRouter free chat model
(``OPENROUTER_CHAT_MODEL``, default ``qwen/qwen3.8-27b:free``) with cited
sources. Full answers are Redis-cached to stretch the free-tier quota
(20/min, 50/day at $0 balance). Both endpoints are owner-scoped like
``GET /urls``: callers only search their own links unless admin.
"""

import hashlib
import time

from flask import Blueprint, abort, current_app, g, jsonify

from app.cache import get_ask_cache, get_search_cache, set_ask_cache, set_search_cache
from app.routes.prometheus import LLM_DURATION, LLM_REQUESTS, LLM_TOKENS, SEMANTIC_CACHE_HITS
from app.utils.auth import cache_scope, is_admin, require_auth
from app.utils.ratelimit import rate_limit
from app.utils.retrieval import (
    ASK_SYSTEM_PROMPT,
    ask_cache_ttl,
    build_ask_context,
    chat_fallback_model,
    chat_model,
    embed_query_cached,
    semantic_search,
)
from app.utils.schemas import AskRequest, SearchQuery, parse_body, parse_query
from shared.openrouter import OpenRouterError, api_key, chat_complete

search_bp = Blueprint("search", __name__)


def _search_scope():
    return cache_scope()


def _actor():
    return {"user_id": g.current_user_id, "is_admin": is_admin()}


def _retrieve(question, k):
    vector, _ = embed_query_cached(question)
    actor = _actor()
    return semantic_search(vector, user_id=actor["user_id"], is_admin=actor["is_admin"], k=k)


@search_bp.route("/search", methods=["GET"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
@rate_limit(capacity=300, refill_rate=5.0)
def search():
    params = parse_query(SearchQuery)
    scope = _search_scope()
    cache_key = hashlib.sha256(f"{params.q}|{params.k}".encode("utf-8")).hexdigest()
    cached = get_search_cache(scope, cache_key)
    if cached is not None:
        SEMANTIC_CACHE_HITS.labels(cache="search", outcome="hit").inc()
        return jsonify(cached)
    SEMANTIC_CACHE_HITS.labels(cache="search", outcome="miss").inc()

    try:
        hits = _retrieve(params.q, params.k)
    except OpenRouterError as exc:
        current_app.logger.warning(f"Semantic search embedding failed: {exc}")
        abort(503, description="Semantic search unavailable (embedding service unreachable)")
    except RuntimeError as exc:
        current_app.logger.warning(f"Semantic search unavailable: {exc}")
        abort(503, description="Semantic search unavailable (requires PostgreSQL + pgvector)")

    payload = {"kind": "search", "query": params.q, "results": hits}
    set_search_cache(scope, cache_key, payload)
    return jsonify(payload)


@search_bp.route("/ask", methods=["POST"])
@rate_limit(capacity=60, refill_rate=1.0)
@require_auth
@rate_limit(capacity=60, refill_rate=1.0)
def ask():
    data = parse_body(AskRequest)
    question, k = data["question"], data.get("k", 5)
    scope = _search_scope()
    cache_key = hashlib.sha256(f"{question}|{k}".encode("utf-8")).hexdigest()
    cached = get_ask_cache(scope, cache_key)
    if cached is not None:
        SEMANTIC_CACHE_HITS.labels(cache="ask", outcome="hit").inc()
        return jsonify(cached)
    SEMANTIC_CACHE_HITS.labels(cache="ask", outcome="miss").inc()

    if not api_key():
        abort(503, description="Q&A unavailable (OPENROUTER_API_KEY is not configured)")

    try:
        hits = _retrieve(question, k)
    except OpenRouterError as exc:
        current_app.logger.warning(f"RAG retrieval embedding failed: {exc}")
        abort(503, description="Q&A unavailable (embedding service unreachable)")
    except RuntimeError as exc:
        current_app.logger.warning(f"RAG retrieval unavailable: {exc}")
        abort(503, description="Q&A unavailable (requires PostgreSQL + pgvector)")

    if not hits:
        payload = {
            "answer": "I couldn't find any saved links relevant to your question.",
            "model": chat_model(),
            "sources": [],
        }
        set_ask_cache(scope, cache_key, payload, ask_cache_ttl())
        return jsonify(payload)

    messages = [
        {"role": "system", "content": ASK_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Saved links:\n{build_ask_context(hits)}\n\nQuestion: {question}",
        },
    ]
    model = chat_model()
    start = time.perf_counter()
    try:
        content, prompt_tokens, completion_tokens, used_model = chat_complete(
            messages, model=model, fallback_model=chat_fallback_model()
        )
    except OpenRouterError as exc:
        LLM_REQUESTS.labels(operation="ask", model=model, status="error").inc()
        LLM_DURATION.labels(operation="ask").observe(time.perf_counter() - start)
        current_app.logger.warning(f"RAG chat failed (model={model}): {exc}")
        status = 503 if (exc.status or 500) in (429, 503) or exc.retryable else 502
        abort(status, description="Q&A unavailable (language model unreachable)")
    LLM_REQUESTS.labels(operation="ask", model=used_model, status="ok").inc()
    LLM_DURATION.labels(operation="ask").observe(time.perf_counter() - start)
    LLM_TOKENS.labels(operation="ask", kind="prompt").inc(prompt_tokens)
    LLM_TOKENS.labels(operation="ask", kind="completion").inc(completion_tokens)

    payload = {"answer": content, "model": used_model, "sources": hits}
    set_ask_cache(scope, cache_key, payload, ask_cache_ttl())
    current_app.logger.info(f"RAG answer synthesized model={used_model} sources={len(hits)}")
    return jsonify(payload)
