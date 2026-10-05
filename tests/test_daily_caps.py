"""The two daily ceilings: one client's share, and the site's whole day.

Groq's free tier answers roughly 27 questions a day and says nothing until it
refuses (docs/deployment.md). These are the site's own limits under that one,
so the refusal a visitor sees is a sentence this app chose rather than a
failure halfway through an answer.

The pipeline is replaced by a canned engine: what is under test is whether a
question is admitted, never what it is answered with.
"""

import pytest

from app import main
from app.config import Settings, settings

PATHS = ["/api/chat", "/api/chat/stream"]


@pytest.fixture
def answering_engine(monkeypatch):
    class Result:
        text = "A canned answer."
        sources = ["resume.md"]

    class Engine:
        def answer(self, question):
            return Result()

        def stream_answer(self, question):
            yield "sources", ["resume.md"]
            yield "token", "A canned answer."
            yield "done", None

    monkeypatch.setattr(main, "get_engine", lambda app: Engine())


def _ask(client, path, ip="203.0.113.7", question="What roles are you looking for?"):
    return client.post(path, json={"question": question}, headers={"x-forwarded-for": ip})


@pytest.mark.parametrize("path", PATHS)
def test_a_client_gets_its_share_and_then_a_fixed_sentence(client, answering_engine, path):
    for _ in range(settings.rate_limit_per_day):
        assert _ask(client, path).status_code == 200
    refused = _ask(client, path)
    assert refused.status_code == 429
    assert refused.json()["detail"] == main._CLIENT_DAILY_MESSAGE


def test_the_share_is_one_allowance_across_both_endpoints(client, answering_engine):
    for i in range(settings.rate_limit_per_day):
        assert _ask(client, PATHS[i % 2]).status_code == 200
    assert _ask(client, "/api/chat").status_code == 429
    assert _ask(client, "/api/chat/stream").status_code == 429


def test_the_share_is_per_client(client, answering_engine):
    for _ in range(settings.rate_limit_per_day):
        _ask(client, "/api/chat", ip="203.0.113.7")
    assert _ask(client, "/api/chat", ip="203.0.113.7").status_code == 429
    assert _ask(client, "/api/chat", ip="203.0.113.8").status_code == 200


def test_a_refused_question_spends_no_share(client, answering_engine):
    """Validation runs first, so an over-long question costs nothing but a minute slot."""
    for _ in range(settings.rate_limit_per_day):
        assert _ask(client, "/api/chat", question="x" * (settings.max_question_chars + 1)).status_code == 413
    for _ in range(settings.rate_limit_per_day):
        assert _ask(client, "/api/chat").status_code == 200


@pytest.mark.parametrize("path", PATHS)
def test_the_global_day_says_back_tomorrow(client, answering_engine, monkeypatch, path):
    monkeypatch.setattr(settings, "global_limit_per_day", 3)
    for i in range(3):
        assert _ask(client, path, ip=f"203.0.113.{i + 1}").status_code == 200
    refused = _ask(client, path, ip="203.0.113.99")
    assert refused.status_code == 429
    assert refused.json()["detail"] == main._DAILY_MESSAGE


def test_a_global_refusal_charges_no_client(client, answering_engine, monkeypatch):
    monkeypatch.setattr(settings, "global_limit_per_day", 1)
    assert _ask(client, "/api/chat", ip="203.0.113.1").status_code == 200
    assert _ask(client, "/api/chat", ip="203.0.113.2").status_code == 429
    assert not main._request_day.get("203.0.113.2"), "a refused question was charged to its client"


def test_the_refusals_are_fixed_sentences():
    """No count and no tuning in them: a refusal is not a readout of the limits."""
    for sentence in (main._CLIENT_DAILY_MESSAGE, main._DAILY_MESSAGE, main._HOURLY_MESSAGE):
        assert not any(ch.isdigit() for ch in sentence)
    assert main._CLIENT_DAILY_MESSAGE != main._DAILY_MESSAGE


def test_the_defaults_sit_under_groqs_daily_budget():
    """200,000 tokens a day at ~7,200 a question is about 27 questions.

    Read from the class, not the instance, so a local .env cannot make this pass.
    """
    fields = Settings.model_fields
    assert fields["global_limit_per_day"].default < 200_000 // 7_200
    assert fields["rate_limit_per_day"].default == 5
    assert fields["rate_limit_per_day"].default < fields["global_limit_per_day"].default
