"""Which sentence a 429 from Groq turns into.

Groq reports a spent minute and a spent day the same way, as a 429 whose body
names the window it enforced. The response headers cannot tell them apart: they
carry the per-minute token window and refill every minute however exhausted the
day is. So the classification is a string match on the body, and these tests are
what keeps it honest, since the failure it guards against is the site telling a
visitor to try again in a moment when nothing will be different tomorrow either.

The bodies below are the shape Groq actually returns, kept verbatim apart from
the organization id. Rewording them to make a test pass is the wrong repair: the
match is against their wording, so a change here is a claim about what Groq sends.
"""

import json

import pytest

from app import main

TPM_BODY = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "`openai/gpt-oss-120b` in organization `org_test` service tier `on_demand` on "
    "tokens per minute (TPM): Limit 8000, Used 7421, Requested 1203. Please try "
    "again in 4.672s. Need more tokens? Upgrade to Dev Tier.', 'type': "
    "'tokens', 'code': 'rate_limit_exceeded'}}"
)

TPD_BODY = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "`openai/gpt-oss-120b` in organization `org_test` service tier `on_demand` on "
    "tokens per day (TPD): Limit 200000, Used 198804, Requested 5163. Please try "
    "again in 1h51m17.28s. Need more tokens? Upgrade to Dev Tier.', 'type': "
    "'tokens', 'code': 'rate_limit_exceeded'}}"
)

RPD_BODY = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "`openai/gpt-oss-120b` in organization `org_test` service tier `on_demand` on "
    "requests per day (RPD): Limit 1000, Used 1000, Requested 1. Please try again "
    "in 3h2m1s.', 'type': 'requests', 'code': 'rate_limit_exceeded'}}"
)


class FakeThrottle(Exception):
    """Stands in for groq's RateLimitError, which is not imported here.

    `_is_upstream_throttle` duck types on `status_code` and on the text, for the
    reason written above it in app/main.py, so this is a faithful stand-in for
    both paths without making groq a direct dependency of the test suite either.
    """

    status_code = 429


def test_the_minute_keeps_the_try_again_sentence():
    assert main._throttle_message(FakeThrottle(TPM_BODY)) == main._THROTTLE_MESSAGE


@pytest.mark.parametrize("body", [TPD_BODY, RPD_BODY], ids=["tokens", "requests"])
def test_the_day_says_tomorrow(body):
    """Both daily windows, because either one ends the day for a visitor."""
    assert main._throttle_message(FakeThrottle(body)) == main._DAILY_MESSAGE


def test_an_unrecognised_body_falls_back_to_the_minute():
    """The match is on the day, so anything it does not recognise is the old behaviour.

    A reworded Groq message, a throttle from a proxy, or a 429 with no body at
    all lands here. Telling someone to wait a minute when the day is gone is the
    failure this module exists to reduce; telling someone the day is gone when it
    is not would be worse, because the rest of the page is the consolation and
    there would be nothing wrong with the chat.
    """
    assert main._throttle_message(FakeThrottle("")) == main._THROTTLE_MESSAGE
    assert main._throttle_message(FakeThrottle("upstream connect error")) == main._THROTTLE_MESSAGE


def test_the_daily_sentence_is_not_the_busy_sentence():
    """Guards the one way this change could be a no-op: two names, one string."""
    assert main._DAILY_MESSAGE != main._THROTTLE_MESSAGE


@pytest.fixture
def throttling_engine(monkeypatch):
    """A pipeline that raises Groq's daily 429 from both entry points."""

    class Engine:
        def answer(self, question):
            raise FakeThrottle(TPD_BODY)

        def stream_answer(self, question):
            raise FakeThrottle(TPD_BODY)
            yield  # pragma: no cover - makes this a generator function

    monkeypatch.setattr(main, "get_engine", lambda app: Engine())


def test_blocking_endpoint_renders_the_daily_sentence(client, throttling_engine):
    response = client.post("/api/chat", json={"question": "What roles is he open to?"})
    assert response.status_code == 429
    assert response.json()["detail"] == main._DAILY_MESSAGE


def test_stream_endpoint_renders_the_daily_sentence(client, throttling_engine):
    """The stream carries it as an error event, since the status line is long gone."""
    response = client.post("/api/chat/stream", json={"question": "What roles is he open to?"})
    assert response.status_code == 200

    events = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events == [{"type": "error", "detail": main._DAILY_MESSAGE}]
