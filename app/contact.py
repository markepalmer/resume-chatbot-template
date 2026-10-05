"""The contact form: validate a visitor's message and relay it to the owner by email.

The visitor's contact details travel exactly one path: the browser, this module,
Resend, the owner's inbox. That path is the whole privacy design, so it is worth
stating what it excludes:

* No model sees a message. Nothing here imports the chat pipeline, and nothing
  is written under ``knowledge/``, so there is nothing for retrieval to find and
  no way for the assistant to repeat a recruiter's phone number to the next
  visitor. A chatbot that could *send* would also be one a prompt injection could
  make send, which is why the form is a plain endpoint and not a tool.
* Nothing is stored. Cloud Run scales to zero and its disk goes with the
  instance, so storing would also be losing; the message is relayed at once.
* Nothing is logged or echoed. Errors raised here carry fixed sentences, never a
  field's value, because an error message is the usual way input leaks into logs
  and responses.

Resend is called with the standard library rather than its SDK. It is one POST,
and a new dependency would mean a lockfile regenerated in a linux/amd64
container (CLAUDE.md), which is a heavy price for fifteen lines.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import settings

RESEND_URL = "https://api.resend.com/emails"

# Deliberately loose. The point is to catch a typo'd or empty field and to refuse
# anything that could carry a header, not to be RFC 5322: a stricter pattern
# rejects real addresses, and the owner replying is the real validation.
_EMAIL = re.compile(r"^[^@\s<>,;:\"()\[\]]+@[^@\s<>,;:\"()\[\]]+\.[^@\s<>,;:\"()\[\]]+$")
_PHONE = re.compile(r"^[0-9+().\s-]+$")

# Any control character, and anything else a parser may treat as a line break.
# Single line fields refuse all of them; the message strips the C0 controls but
# keeps newline and tab. CR and LF are the ones that matter most: a name or
# address carrying them into an email header is header injection, where the
# visitor writes extra headers (a Bcc, say) into mail sent under the site's key.
# U+0085 (NEL, in the C1 block), U+2028 and U+2029 are line breaks to Unicode
# aware parsers and to `str.splitlines`, so they are refused alongside. Resend
# takes JSON rather than raw headers, so this is defence in depth, but it is the
# defence that does not depend on a third party's parser.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_CONTROL_EXCEPT_NEWLINE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class ContactError(ValueError):
    """A message was refused. The text is a fixed sentence, safe to show the sender."""


class ContactSendError(RuntimeError):
    """Resend did not accept the message. Never carries the message itself."""


@dataclass(frozen=True)
class ContactMessage:
    name: str
    email: str
    phone: str
    message: str


def is_configured() -> bool:
    """Both halves are needed: a key with nowhere to send is as useless as the reverse."""
    return bool(settings.resend_api_key and settings.contact_to)


def _line(value: str, label: str) -> str:
    value = value.strip()
    if _CONTROL.search(value):
        raise ContactError(f"{label} must be a single line.")
    if len(value) > settings.max_contact_field_chars:
        raise ContactError(
            f"{label} is too long (max {settings.max_contact_field_chars} characters)."
        )
    return value


def clean(name: str, email: str, phone: str, message: str) -> ContactMessage:
    """Validate and normalise a submission, or raise ContactError."""
    name = _line(name, "Name")
    email = _line(email, "Email")
    phone = _line(phone, "Phone")

    message = message.replace("\r\n", "\n").strip()
    message = _CONTROL_EXCEPT_NEWLINE.sub("", message)

    if not name:
        raise ContactError("Please add your name.")
    if not message:
        raise ContactError("Please write a message.")
    if len(message) > settings.max_contact_message_chars:
        raise ContactError(
            f"Message is too long (max {settings.max_contact_message_chars} characters)."
        )
    if not email and not phone:
        raise ContactError("Please leave an email address or a phone number to reply to.")
    if email and not _EMAIL.match(email):
        raise ContactError("That email address does not look right.")
    if phone and not (_PHONE.match(phone) and 7 <= sum(c.isdigit() for c in phone) <= 15):
        # 15 is the E.164 maximum, country code included.
        raise ContactError("That phone number does not look right.")

    return ContactMessage(name=name, email=email, phone=phone, message=message)


def _body(msg: ContactMessage) -> str:
    lines = [f"From: {msg.name}"]
    if msg.email:
        lines.append(f"Email: {msg.email}")
    if msg.phone:
        lines.append(f"Phone: {msg.phone}")
    lines += ["", msg.message, "", "Sent from the contact form on the resume site."]
    return "\n".join(lines)


def send(msg: ContactMessage) -> None:
    """Relay one message to the owner through Resend, or raise ContactSendError.

    Blocking network IO, so this is called from a sync handler, which Starlette
    runs in a worker thread (CLAUDE.md, the sync/async convention).

    Plain text only. An HTML body would be a second place for the visitor's text
    to need escaping, in exchange for nothing.
    """
    payload = {
        "from": settings.contact_from,
        "to": [settings.contact_to],
        "subject": f"Resume site message from {msg.name}",
        "text": _body(msg),
    }
    # Reply lands on the visitor rather than on the sending address, so the owner
    # can answer from the inbox. A phone-only message has nothing to put here.
    if msg.email:
        payload["reply_to"] = msg.email

    request = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            # Resend sits behind Cloudflare, which refuses urllib's default
            # "Python-urllib/3.x" agent outright, as a 403 that looks like a bad key.
            "User-Agent": "resume-site-contact/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        # Status only. The response body can quote the payload back, and the
        # payload is exactly what must not reach the logs.
        logging.error("contact relay refused by Resend: HTTP %s", exc.code)
        raise ContactSendError("relay refused") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        logging.error("contact relay unreachable: %s", type(exc).__name__)
        raise ContactSendError("relay unreachable") from None

    if not 200 <= status < 300:
        logging.error("contact relay returned HTTP %s", status)
        raise ContactSendError("relay refused")
