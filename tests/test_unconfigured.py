"""What the app does with no GROQ_API_KEY set.

This is the fork path. Somebody clones the repo, runs it, and has not signed up
for Groq yet. Until 2026-08-31 that raised in ``lifespan``, so their first
experience of the project was a stack trace and a decision about whether to
register with a third party on the strength of a README. It now boots, serves the
page with their own name on it, and answers questions with a 503 that says which
variable is missing.

Both halves of that need pinning. The page serving is the point of the change,
and the 503 is what stops the change from turning a misconfigured deploy into a
silently broken one. Neither was under test before, in either direction, because
``conftest`` skips lifespan for the whole suite.
"""

import logging

import pytest
from fastapi.testclient import TestClient

CHAT_ENDPOINTS = ("/api/chat", "/api/chat/stream")


@pytest.fixture
def unconfigured(monkeypatch):
    """Blank the key on the shared settings object for one test.

    ``app.main`` does ``from .config import settings``, so this is the same
    instance the request path reads; there is no second copy to keep in step.
    monkeypatch restores the real value afterwards, which matters because the
    developer running this suite probably has a working key in .env.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "")
    return settings


def test_lifespan_boots_without_a_key(unconfigured, reset_limiter_state, caplog):
    """The context manager is entered on purpose here; the rest of the suite does not.

    Startup is the behaviour under test, so this is the one place that has to run
    it. A regression to the old ``raise`` shows up as an error out of ``__enter__``
    rather than as a failed assertion.
    """
    from app import main

    with caplog.at_level(logging.WARNING):
        with TestClient(main.app) as client:
            assert client.get("/health").status_code == 200

    assert any("GROQ_API_KEY" in record.getMessage() for record in caplog.records), (
        "startup did not warn about the missing key. The warning is the only thing "
        "that makes a keyless production revision noticeable before a visitor "
        "tries to chat, and it is what was traded for the old fail-fast raise."
    )


def test_page_renders_without_a_key(unconfigured, client):
    """The whole point of the change: a forker sees their site before signing up."""
    response = client.get("/")
    assert response.status_code == 200
    assert len(response.text) > 1000, (
        "the page came back without a key but is suspiciously short, which would "
        "mean it renders an error rather than the site"
    )


@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_both_chat_endpoints_503_without_a_key(unconfigured, client, no_engine, path):
    """CLAUDE.md: guardrails live in _check_request so they cannot drift apart.

    The streaming endpoint returns a real status code here rather than an error
    event, because ``_check_request`` runs in the async body before the generator
    is handed to StreamingResponse, so the response has not started yet.
    """
    response = client.post(path, json={"question": "What do you do?"})
    assert response.status_code == 503, (
        f"{path} returned {response.status_code} with no key set. Both endpoints "
        "must answer through the same guard."
    )
    assert "GROQ_API_KEY" in response.json()["detail"], (
        "the 503 does not name the missing variable. Vague is useless here: the "
        "only people who see this message are the ones who can fix it."
    )


@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_unconfigured_503_spends_nothing(unconfigured, client, no_engine, path):
    """The config check runs ahead of both limiters, and must stay there.

    A missing key is the server's fault, not the caller's. If this check moves
    below the limiters, a keyless deployment burns its own daily ceiling
    answering nothing, and a visitor gets rate-limited for a mistake they did not
    make. Both failures are invisible from the response, which is why the
    accounting is asserted rather than the status code alone.
    """
    from app import main

    for _ in range(3):
        assert client.post(path, json={"question": "hello"}).status_code == 503

    assert not main._global_day, "a 503 for a missing key was charged to the daily budget"
    assert not main._global_hour, "a 503 for a missing key was charged to the hourly budget"
    assert not main._request_log, "a 503 for a missing key consumed the caller's rate limit"
    assert not main._request_day, "a 503 for a missing key consumed the caller's daily share"


def test_guard_does_not_fire_when_a_key_is_set(client, no_engine):
    """The other direction: a configured app must not 503 its way through.

    Without this, deleting the ``if`` in ``_check_request`` and hardcoding a 503
    would pass every test above.
    """
    response = client.post("/api/chat", json={"question": "   "})
    assert response.status_code == 400, (
        f"a configured app returned {response.status_code} for an empty question, "
        "not the 400 the validation guard owes it"
    )
