"""Destination policy and opt-in reputation checks. Never fetch destination URLs.

Lexical validation cannot establish where a domain resolves. Any future preview
fetcher must implement its own DNS/IP pinning and redirect policy; this is not a
network client for arbitrary URLs.
"""

import hashlib
import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError as TransportError
from flask import abort, current_app

_PROVIDER_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
_MAX_RESPONSE = 65536

# Reputation verdicts are cached so a redirect is a cache read, not a
# synchronous provider round trip.  Only the two definitive verdicts are
# stored: "unavailable" describes a provider failure and is resolved by
# SAFE_BROWSING_FAILURE_POLICY on every call, never pinned for an hour.
_VERDICT_CACHE_PREFIX = "reputation:v1:"
_CACHEABLE_VERDICTS = ("no_match", "unsafe")
_DEFAULT_CACHE_TTL = 3600


def validate_destination(value):
    """Reject unsafe schemes, local literals/names and ambiguous URL spellings."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Destination must be an http(s) URL")
    if any(ord(c) <= 32 or ord(c) == 127 or c.isspace() for c in value) or "\\" in value:
        raise ValueError("Destination contains whitespace or control characters")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid destination authority") from exc
    if parsed.scheme not in ("http", "https") or not host or port == 0:
        raise ValueError("Destination must be an http(s) URL")
    if parsed.username is not None or parsed.password is not None or "%" in host:
        raise ValueError("Destination credentials and encoded hosts are not allowed")
    host = host.rstrip(".").lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # inet_aton recognizes shortened, octal and hexadecimal IPv4 spellings
        # without resolving DNS. Reject even public noncanonical numeric hosts.
        try:
            socket.inet_aton(host)
        except OSError:
            pass
        else:
            raise ValueError("Noncanonical numeric hosts are not allowed")
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Invalid destination host") from exc
        labels = ascii_host.split(".")
        if (
            len(labels) < 2
            or len(ascii_host) > 253
            or not re.fullmatch(r"[a-z]{2,63}|xn--[a-z0-9-]+", labels[-1])
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
            )
        ):
            raise ValueError("Destination must use a public hostname")
        if any(
            host == suffix or host.endswith("." + suffix)
            for suffix in (
                "localhost",
                "local",
                "internal",
                "lan",
                "home",
                "test",
                "invalid",
                "onion",
                "home.arpa",
                "metadata.google.internal",
            )
        ):
            raise ValueError("Local destinations are not allowed")
    else:
        mapped = getattr(address, "ipv4_mapped", None)
        if not address.is_global or (mapped is not None and not mapped.is_global):
            raise ValueError("Non-public IP destinations are not allowed")
    return value


def _setting(name, default=""):
    return current_app.config.get(name, os.environ.get(name, default))


def _verdict_cache_ttl():
    """Verdict TTL in seconds; a non-positive value disables caching."""
    try:
        return int(_setting("SAFE_BROWSING_CACHE_TTL", _DEFAULT_CACHE_TTL))
    except (TypeError, ValueError):
        # A misspelled TTL must not silently disable the cache or crash a
        # redirect; fall back to the documented default.
        return _DEFAULT_CACHE_TTL


def _verdict_cache_key(value):
    """Key on a digest so raw destinations never reach Redis or the logs."""
    return _VERDICT_CACHE_PREFIX + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cached_verdict(key, ttl):
    """Return a cached verdict from L1 then L2, or None on a genuine miss.

    L1 is the same bounded per-process LRU the rest of the app uses, so the
    redirect path keeps working (and keeps not re-blocking on the provider)
    when Redis is unavailable.
    """
    from app import cache

    verdict, _ = cache._l1_get(key)
    if verdict in _CACHEABLE_VERDICTS:
        return verdict
    stored = cache._l2_safe(lambda client, k=key: client.get(k))
    if stored in _CACHEABLE_VERDICTS:
        cache._l1_set(key, stored, ttl)
        return stored
    return None


def _store_verdict(key, verdict, ttl):
    from app import cache

    cache._l1_set(key, verdict, ttl)
    cache._l2_fire_and_forget(lambda client, k=key, v=verdict, t=ttl: client.setex(k, t, v))


def reputation_status(value):
    """Return disabled / no_match / unsafe / unavailable, never a 'safe' guarantee.

    Explicit opt-in sends the full destination (including its query) to Google.
    Fixed endpoint, no redirects/proxies/retries; bounded response and timeouts.
    Verdicts (both safe and unsafe) are cached for SAFE_BROWSING_CACHE_TTL
    seconds so the synchronous call happens on a cache miss only.
    """
    provider = _setting("SAFE_BROWSING_PROVIDER", "disabled")
    if provider == "disabled":
        return "disabled"
    if provider != "google" or not _setting("SAFE_BROWSING_API_KEY"):
        return "unavailable"

    ttl = _verdict_cache_ttl()
    key = _verdict_cache_key(value)
    if ttl > 0:
        cached = _cached_verdict(key, ttl)
        if cached is not None:
            return cached

    verdict = _query_provider(value)
    if ttl > 0 and verdict in _CACHEABLE_VERDICTS:
        _store_verdict(key, verdict, ttl)
    return verdict


def _query_provider(value):
    """Ask the provider once. Provider failures surface as "unavailable"."""
    payload = {
        "client": {"clientId": "quadrourl", "clientVersion": "1"},
        "threatInfo": {
            "threatTypes": [
                "MALWARE",
                "SOCIAL_ENGINEERING",
                "UNWANTED_SOFTWARE",
                "POTENTIALLY_HARMFUL_APPLICATION",
            ],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": value}],
        },
    }
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.post(
                _PROVIDER_URL,
                params={"key": _setting("SAFE_BROWSING_API_KEY")},
                json=payload,
                timeout=(2, 3),
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.status_code != 200:
                    return "unavailable"
                # A single bounded raw read also avoids unlimited streamed chunks.
                raw = response.raw.read(_MAX_RESPONSE + 1, decode_content=True)
                if len(raw) > _MAX_RESPONSE:
                    return "unavailable"
                result = json.loads(raw)
                if not isinstance(result, dict) or set(result) - {"matches"}:
                    return "unavailable"
                matches = result.get("matches", [])
                if not isinstance(matches, list):
                    return "unavailable"
                return "unsafe" if matches else "no_match"
    except (requests.RequestException, TransportError, ValueError, OSError):
        return "unavailable"


def require_destination_safe(value):
    try:
        validate_destination(value)
    except ValueError as exc:
        abort(400, description=str(exc))
    status = reputation_status(value)
    if status == "unsafe":
        abort(403, description="Destination blocked by reputation provider")
    if status == "unavailable":
        # A cached verdict is not a failure; only a real provider failure gets
        # here.  Misspelled or missing failure policies fail closed as well.
        if _setting("SAFE_BROWSING_FAILURE_POLICY", "closed") != "open":
            abort(503, description="Destination reputation check unavailable")
        current_app.logger.warning("Reputation provider unavailable; configured fail-open policy")
    return status
