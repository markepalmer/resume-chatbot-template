"""The contact form's endpoint, validation, and the promises about a visitor's details.

No network: ``contact.send`` is replaced by a recorder in every test that gets
far enough to call it, and the relay's own request is checked by stubbing
``urlopen`` rather than by reaching Resend.
"""

import json
import logging

import pytest

from app import contact, main

GOOD = {"name": "Ada Recruiter", "email": "ada@example.com", "message": "Are you open to a call?"}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(main.settings, "resend_api_key", "re_test_not_used")
    monkeypatch.setattr(main.settings, "contact_to", "owner@example.com")


@pytest.fixture
def sent(monkeypatch):
    """Record what would have been relayed instead of relaying it."""
    outbox = []
    monkeypatch.setattr(contact, "send", outbox.append)
    return outbox


def test_unconfigured_is_503_and_spends_nothing(client, sent, monkeypatch):
    monkeypatch.setattr(main.settings, "resend_api_key", "")
    response = client.post("/api/contact", json=GOOD)
    assert response.status_code == 503
    assert not sent
    assert not main._contact_log and not main._contact_day, (
        "a server-side misconfiguration was charged to the caller's window"
    )


def test_unconfigured_page_has_no_form(client, monkeypatch):
    monkeypatch.setattr(main.settings, "resend_api_key", "")
    assert 'id="contact-form"' not in client.get("/").text


def test_configured_page_has_the_form(client, configured):
    assert 'id="contact-form"' in client.get("/").text


def test_a_good_message_is_relayed(client, configured, sent):
    response = client.post("/api/contact", json=GOOD)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert sent == [contact.ContactMessage("Ada Recruiter", "ada@example.com", "", GOOD["message"])]


def test_phone_alone_is_enough(client, configured, sent):
    body = {"name": "Ada", "phone": "+1 (555) 010-0199", "message": "Call me."}
    assert client.post("/api/contact", json=body).status_code == 200
    assert sent[0].phone == "+1 (555) 010-0199"


@pytest.mark.parametrize(
    "override",
    [
        {"email": "", "phone": ""},
        {"email": "not an address"},
        {"email": "a@b.c\r\nBcc: victim@example.com"},
        {"name": "Ada\nBcc: victim@example.com"},
        {"name": "Ada\u2028Bcc: victim@example.com"},
        {"name": "Ada\u2029Bcc: victim@example.com"},
        {"email": "a@b.c\u0085Bcc: victim@example.com"},
        {"phone": "555\u20280100 123"},
        {"phone": "12"},
        {"name": ""},
        {"message": "   "},
        {"message": "x" * 3001},
    ],
)
def test_bad_submissions_are_refused_and_not_sent(client, configured, sent, override):
    response = client.post("/api/contact", json={**GOOD, **override})
    assert response.status_code == 400
    assert not sent


def test_refusals_never_echo_the_input(client, configured, sent):
    """The error is shown to the sender and may reach logs; it must not quote them."""
    body = {**GOOD, "email": "leaky-secret@nowhere"}
    response = client.post("/api/contact", json=body)
    assert response.status_code == 400
    assert "leaky-secret" not in response.text


def test_a_non_string_field_does_not_422_with_the_input(client, configured, sent):
    """Pydantic's 422 quotes the input back, so the model must not raise one for a missing field."""
    response = client.post("/api/contact", json={"message": "hi"})
    assert response.status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {**GOOD, "name": 12345, "email": "leaky-secret@nowhere"},
        {**GOOD, "message": ["leaky-secret"]},
        ["leaky-secret"],
        "leaky-secret",
    ],
)
def test_a_malformed_body_gets_a_fixed_sentence_and_no_echo(client, configured, sent, body):
    """A wrong type or a non-object body still fails validation, and FastAPI's
    422 would quote it back. The contact route answers with one fixed sentence."""
    response = client.post("/api/contact", json=body)
    assert response.status_code == 400
    assert response.json() == {"detail": main._CONTACT_UNREADABLE}
    assert "leaky-secret" not in response.text
    assert not sent


def test_broken_json_gets_the_same_sentence(client, configured, sent):
    response = client.post(
        "/api/contact",
        content=b'{"name": "leaky-secret',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert "leaky-secret" not in response.text


def test_other_routes_keep_the_default_validation_error(client, no_engine):
    """The handler is scoped to the contact route: chat keeps FastAPI's 422."""
    assert client.post("/api/chat", json={"question": 123}).status_code == 422


def test_honeypot_looks_like_success_and_sends_nothing(client, configured, sent):
    response = client.post("/api/contact", json={**GOOD, "website": "http://spam.example"})
    assert response.status_code == 200
    assert not sent


def test_per_ip_limit(client, configured, sent):
    for _ in range(main.settings.contact_limit_per_hour):
        assert client.post("/api/contact", json=GOOD).status_code == 200
    assert client.post("/api/contact", json=GOOD).status_code == 429
    assert len(sent) == main.settings.contact_limit_per_hour


def test_daily_ceiling(client, configured, sent, monkeypatch):
    monkeypatch.setattr(main.settings, "contact_limit_per_day", 1)
    assert client.post("/api/contact", json=GOOD).status_code == 200
    # A second address, so the per-IP window is not what refuses it.
    headers = {"x-forwarded-for": "203.0.113.9"}
    assert client.post("/api/contact", json=GOOD, headers=headers).status_code == 429


def test_contact_limits_are_separate_from_chat(client, configured, sent):
    client.post("/api/contact", json=GOOD)
    assert not main._request_log and not main._global_day


def test_relay_failure_is_502_with_a_fixed_sentence(client, configured, monkeypatch):
    def fail(msg):
        raise contact.ContactSendError("relay refused")

    monkeypatch.setattr(contact, "send", fail)
    response = client.post("/api/contact", json=GOOD)
    assert response.status_code == 502
    assert "Ada" not in response.text


def test_contact_endpoint_is_sync():
    import inspect

    assert not inspect.iscoroutinefunction(main.contact_owner), (
        "/api/contact is `async def`, which puts a blocking HTTPS call to Resend "
        "on the event loop"
    )


# --- the relay itself, with urlopen stubbed -------------------------------------


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_send_builds_the_resend_request(configured, monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        return _FakeResponse()

    monkeypatch.setattr(contact.urllib.request, "urlopen", fake_urlopen)
    contact.send(contact.ContactMessage("Ada", "ada@example.com", "", "Hello"))

    request = seen["request"]
    payload = json.loads(request.data)
    assert request.full_url == contact.RESEND_URL
    assert request.get_header("Authorization") == "Bearer re_test_not_used"
    assert request.get_header("User-agent", "").startswith("resume-site-contact")
    assert payload["to"] == ["owner@example.com"]
    assert payload["reply_to"] == "ada@example.com"
    assert "Hello" in payload["text"]


def test_phone_only_message_has_no_reply_to(configured, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        contact.urllib.request, "urlopen",
        lambda request, timeout: seen.setdefault("r", request) and _FakeResponse(),
    )
    contact.send(contact.ContactMessage("Ada", "", "5550100199", "Hello"))
    assert "reply_to" not in json.loads(seen["r"].data)


def test_send_failure_logs_status_not_content(configured, monkeypatch, caplog):
    import urllib.error

    def refuse(request, timeout):
        raise urllib.error.HTTPError(contact.RESEND_URL, 422, "bad", {}, None)

    monkeypatch.setattr(contact.urllib.request, "urlopen", refuse)
    with caplog.at_level(logging.ERROR), pytest.raises(contact.ContactSendError):
        contact.send(contact.ContactMessage("Ada", "secret@example.com", "", "private words"))
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "422" in logged
    assert "secret@example.com" not in logged and "private words" not in logged


def test_contact_module_does_not_reach_the_pipeline():
    """The privacy design in one assertion: the relay cannot hand a message to a model."""
    source = (main.ROOT_DIR / "app" / "contact.py").read_text(encoding="utf-8")
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    for line in imports:
        for forbidden in ("chat", "retriever", "ingest", "prompts", "langchain", "groq"):
            assert forbidden not in line, f"app/contact.py imports {forbidden!r}: {line}"
    assert "knowledge_dir" not in source
