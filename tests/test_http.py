"""The HTTP surface, exercised without ever building the chat engine.

Every test here runs against the real app object. None of them constructs a
ChatEngine: the routes under test either do not need one, or have `get_engine`
monkeypatched out so the guardrail can be observed rejecting the request before
the pipeline would have been reached. That is not only for speed. `get_engine`
downloads an embedding model and builds a FAISS index, and the endpoints behind
it make two Groq calls, so a test that reached it would need a network and an
API key to answer a question about rate limiting.
"""

import json

import pytest

from app.config import settings

SECURITY_HEADERS = (
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "X-Frame-Options",
    "Permissions-Policy",
    "Strict-Transport-Security",
)


def test_health_is_200(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_answers_head(client):
    """Uptime monitors ping with HEAD; a GET-only route answers those with 405."""
    assert client.head("/health").status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_interactive_docs_are_off(client, path):
    """docs_url, redoc_url and openapi_url are all None together.

    /docs and /redoc are only renderers over /openapi.json, so leaving the
    schema up undoes the other two. The schema title carries the owner's full
    legal name and the request shapes of a four-route private site.
    """
    assert client.get(path).status_code == 404, (
        f"{path} is reachable. All three of docs_url, redoc_url and openapi_url "
        "must be None; disabling only the renderers leaves the schema published."
    )


def test_all_six_security_headers_present(client):
    response = client.get("/health", headers={"x-forwarded-proto": "https"})
    missing = [h for h in SECURITY_HEADERS if h not in response.headers]
    assert not missing, f"security headers missing from the response: {missing}"


def test_hsts_only_on_https(client):
    """Sent over plain http it is ignored by browsers, and poisons localhost."""
    plain = client.get("/health")
    assert "Strict-Transport-Security" not in plain.headers, (
        "HSTS was emitted on a plain-http request. In local development that "
        "pins localhost to https in the developer's own browser for a year."
    )


def test_csp_locks_down_script_src(client):
    csp = client.get("/health").headers["Content-Security-Policy"]
    assert "script-src 'self' 'nonce-" in csp, (
        "script-src must stay nonce-gated and same-origin. Widening it is the one "
        "thing this CSP exists to prevent."
    )
    for directive in ("object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'"):
        assert directive in csp, f"CSP lost {directive!r}"


def test_oversized_body_is_rejected(client, no_engine):
    """Rejected from Content-Length, before anything parses the JSON.

    max_question_chars cannot do this job: it runs in _check_request, which
    FastAPI does not reach until it has already read and deserialized the whole
    body.
    """
    body = json.dumps({"question": "x" * (settings.max_body_bytes + 1024)})
    assert len(body.encode()) > settings.max_body_bytes
    response = client.post("/api/chat", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 413, (
        f"a {len(body)}-byte body was not rejected against the "
        f"{settings.max_body_bytes}-byte ceiling"
    )


def test_overlong_question_is_rejected(client, no_engine):
    question = "x" * (settings.max_question_chars + 1)
    response = client.post("/api/chat", json={"question": question})
    assert response.status_code == 413, (
        f"a {len(question)}-character question passed the "
        f"{settings.max_question_chars}-character cap"
    )


def test_empty_question_is_rejected(client, no_engine):
    assert client.post("/api/chat", json={"question": "   "}).status_code == 400


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/stream"])
def test_rate_limit_is_shared_by_both_chat_endpoints(client, no_engine, path):
    """CLAUDE.md: guardrails live in _check_request so they cannot drift apart.

    An over-long question is used as the payload so the limiter is exercised
    without the pipeline ever being reached: each request is counted by
    _rate_limited before the length cap rejects it.
    """
    payload = {"question": "x" * (settings.max_question_chars + 1)}
    codes = [
        client.post(path, json=payload).status_code
        for _ in range(settings.rate_limit_per_minute + 1)
    ]
    accepted = codes[: settings.rate_limit_per_minute]
    assert all(c != 429 for c in accepted), (
        f"{path} rate-limited before {settings.rate_limit_per_minute} requests: {codes}"
    )
    assert codes[-1] == 429, (
        f"{path} did not return 429 on request "
        f"{settings.rate_limit_per_minute + 1} of a 60-second window: got {codes[-1]}. "
        "The per-IP limit is what protects the free Groq quota."
    )


def test_index_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert len(response.text) > 1000, "the page rendered but is suspiciously short"
