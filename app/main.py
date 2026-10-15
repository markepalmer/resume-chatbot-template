"""FastAPI app: serves the static site and the grounded chat endpoint.

Routes:
    GET  /                  -> the single-page site (static/index.html)
    GET  /health            -> 200, a liveness probe. Nothing pings it on a
                               schedule: Cloud Run scales to zero and wakes on
                               request, so a keep-alive would only buy uptime
                               nobody asked for at the cost of never idling.
    POST /api/chat          -> {question} -> {answer, sources}
    POST /api/chat/stream   -> {question} -> server-sent events, the answer token by token
    POST /api/contact       -> {name, email, phone, message} -> {ok}, relayed by email

The ChatEngine (which builds the FAISS index and the Groq clients) is created on
first use, not at startup, and reused for every request after that. See
``lifespan`` and ``get_engine`` below for why: the page does not need it, and on a
scale-to-zero platform the page is what most visitors came for.
"""

import ipaddress
import json
import logging
import secrets
import threading
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import contact
from .chat import ChatEngine
from .config import ROOT_DIR, settings
from .profile import (
    load_career_arc_svg,
    load_eval_panel_data,
    load_eval_panel_svg,
    load_profile,
    load_results_chart_svg,
    load_resume_html,
    person_ld_json,
    starter_answers_json,
)

templates = Jinja2Templates(directory=str(ROOT_DIR / "templates"))

# Simple in-memory sliding-window rate limiter keyed by client IP. Good enough to
# protect the free Groq quota on a single-container deployment; not a distributed limiter.
#
# Plain dict rather than a defaultdict: an entry has to be removable once its
# window empties. A defaultdict here grows one deque per address seen and never
# sheds them, which is a slow leak under normal traffic and a fast one under a
# scan, since the key is attacker-chosen.
_request_log: dict[str, deque[float]] = {}

# Accepted questions per client over a rolling day: `rate_limit_per_day`. Keyed
# and swept like `_request_log`, for the same reason.
_request_day: dict[str, deque[float]] = {}

# Accepted requests across all clients, for the global ceiling. Two windows so an
# hourly burst and a slow all-day drip are both bounded.
_global_hour: deque[float] = deque()
_global_day: deque[float] = deque()

# The contact form's own windows, kept apart from the chat's: a person writes one
# message, not twenty a minute, and the ceiling protects an inbox and Resend's
# free tier rather than the Groq budget.
_contact_log: dict[str, deque[float]] = {}
_contact_day: deque[float] = deque()

# One lock over every map and deque above. `/api/chat` is `def`, so its guard
# runs in Starlette's threadpool, and `/api/chat/stream` runs its guard on the
# event loop, so these are touched from several threads at once. Unlocked,
# `_sweep` walking a dict while another thread inserted raised "dictionary
# changed size during iteration", and a check-then-append let concurrent
# requests each see room and overshoot a limit. One lock rather than one per
# map, because `_sweep` walks them all. The critical sections are arithmetic
# only, no IO and no logging, and none calls another, since this is not
# reentrant.
_limiter_lock = threading.Lock()


def _bucket(address: str) -> str:
    """The rate-limit key for an address: IPv4 as it is, IPv6 as its /64.

    One IPv6 subscriber is routinely handed a whole /64 and can pick any address
    in it, so a key on the full address hands one client 2**64 fresh buckets and
    every per-client limit stops being one. An IPv4-mapped address is its IPv4,
    and anything that does not parse (the test client, say) is kept as it is.
    """
    try:
        ip = ipaddress.ip_address(address.strip("[]"))
    except ValueError:
        return address
    if ip.version == 4:
        return address
    if ip.ipv4_mapped:
        return str(ip.ipv4_mapped)
    return str(ipaddress.IPv6Network((int(ip) >> 64 << 64, 64)))


def client_ip(request: Request) -> str:
    """The requesting client's rate-limit key, as far as it can be trusted.

    An address for IPv4 and a /64 for IPv6; see ``_bucket``.

    ``request.client.host`` is the socket peer, which behind a reverse proxy is
    the proxy itself. Left alone, every visitor collapses into one bucket and the
    per-IP limit silently becomes a global one that any single user can exhaust
    for everyone.

    X-Forwarded-For is a client-writable header, so it is only usable because a
    trusted proxy appends the peer it actually saw. With ``trusted_proxy_hops``
    of N, the Nth entry from the right is the last one written by infrastructure
    we control; everything left of it is unverified client input. Reading the
    leftmost entry, which is the common shortcut, hands every client the ability
    to pick its own rate-limit bucket.
    """
    peer = request.client.host if request.client else "unknown"
    hops = settings.trusted_proxy_hops
    if hops <= 0:
        return _bucket(peer)

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return _bucket(peer)
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    if len(parts) < hops:
        # Fewer hops than configured: the request did not arrive through the
        # expected chain, so nothing in the header is vouched for.
        return _bucket(peer)
    return _bucket(parts[-hops])


def request_origin(request: Request) -> str:
    """The absolute origin this response is being served from, scheme included.

    ``request.base_url`` alone reports ``http`` behind Cloud Run. TLS is
    terminated at the front end and the container is spoken to over plain HTTP,
    so the scheme the app sees is the scheme of the *last* hop rather than the
    one in the address bar. The app is not run with uvicorn's ``--proxy-headers``
    on purpose: that middleware also rewrites ``scope["client"]``, which is the
    fallback ``client_ip`` reads when the forwarded chain is shorter than
    ``trusted_proxy_hops``, and the rate limiter is not worth disturbing to fix a
    meta tag.

    ``X-Forwarded-Proto`` is read leftmost, which is the scheme the original
    client used. A client can of course send whatever it likes, but the only
    thing this decides is a URL inside that same client's own response: there is
    no shared cache in front of this page for a forged value to poison.
    """
    origin = str(request.base_url).rstrip("/")
    proto = request.headers.get("x-forwarded-proto", "")
    scheme = proto.split(",")[0].strip().lower()
    if scheme in ("http", "https") and "://" in origin:
        return scheme + "://" + origin.split("://", 1)[1]
    return origin


def _prune(window: deque[float], now: float, span: float) -> None:
    while window and now - window[0] > span:
        window.popleft()


def _rate_limited(client: str) -> bool:
    with _limiter_lock:
        now = time.monotonic()
        window = _request_log.get(client)
        if window is None:
            window = _request_log[client] = deque()
        _prune(window, now, 60.0)
        if len(window) >= settings.rate_limit_per_minute:
            return True
        window.append(now)
        return False


_last_sweep = 0.0


def _sweep(now: float) -> None:
    """Drop buckets whose window has aged out, so the map cannot grow without bound.

    This has to be a sweep rather than a check on the owning client's next
    request: an address that is seen once and never again would keep its bucket
    forever, and single-shot addresses are exactly what a scan produces. Runs at
    most once a minute, and the map it walks is bounded by the traffic of that
    minute.
    """
    global _last_sweep
    with _limiter_lock:
        if now - _last_sweep < 60.0:
            return
        _last_sweep = now
        for key in [k for k, w in _request_log.items() if not w or now - w[-1] > 60.0]:
            del _request_log[key]
        for key in [k for k, w in _request_day.items() if not w or now - w[-1] > 86400.0]:
            del _request_day[key]
        for key in [k for k, w in _contact_log.items() if not w or now - w[-1] > 3600.0]:
            del _contact_log[key]


def _spend(client: str) -> str | None:
    """Charge one accepted question, or return the sentence for the window that is full.

    Three windows: this client's day, then the global day, then the global hour.
    Nothing is charged unless all three have room, so a refusal costs the caller
    nothing and a global refusal does not eat into anyone's own share.
    """
    with _limiter_lock:
        now = time.monotonic()
        mine = _request_day.get(client)
        if mine is None:
            mine = _request_day[client] = deque()
        _prune(mine, now, 86400.0)
        _prune(_global_hour, now, 3600.0)
        _prune(_global_day, now, 86400.0)
        if len(mine) >= settings.rate_limit_per_day:
            return _CLIENT_DAILY_MESSAGE
        if len(_global_day) >= settings.global_limit_per_day:
            return _DAILY_MESSAGE
        if len(_global_hour) >= settings.global_limit_per_hour:
            return _HOURLY_MESSAGE
        mine.append(now)
        _global_hour.append(now)
        _global_day.append(now)
        return None


_engine_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start serving immediately; build the chat engine on first use.

    The engine loads an ONNX embedding model and builds the FAISS index, which is
    a few seconds of work. Doing it here used to mean the container could not
    answer *anything* until it finished, so on a scale-to-zero platform every
    visitor arriving after an idle period waited for the whole RAG stack before
    the page could render, despite the page not using it. Deferring it moves that
    cost onto the first chat, where the thinking indicator already covers it.

    A missing GROQ_API_KEY warns here but does not raise. It used to, on the
    argument that a keyless deploy should fail fast. That is right for this
    deployment and wrong for the repo, which is also a template: a forker who has
    not signed up for Groq yet got a stack trace instead of their own site, and
    had to trust a stranger's README enough to register with a third party before
    seeing anything. Booting anyway lets them see the page with their name on it
    first; the two chat endpoints return a 503 that says exactly what is missing
    (see ``_check_request``).

    Production still notices immediately. This warning lands in the logs at
    startup, and the first chat request 503s rather than answering, so the window
    where a keyless revision looks healthy is one request wide. What is given up
    is Cloud Run's "the revision never starts, so traffic stays on the old one"
    behaviour, which is a real loss and the reason this is documented rather than
    quietly changed. It is bought back by the deploy checklist in
    ``docs/deployment.md``, which reads /api/chat once against every new revision.
    """
    if not settings.groq_api_key:
        logging.warning(
            "GROQ_API_KEY is not set. The site will serve, but the assistant will "
            "return 503 for every question until a key is set in the environment "
            "(a Secret Manager secret in production, a .env file locally)."
        )
    app.state.engine = None
    yield


def get_engine(app: FastAPI) -> ChatEngine:
    """Return the shared ChatEngine, building it on first call.

    Double-checked locking, because the callers are threads: the sync ``/api/chat``
    handler and the sync generator behind the streaming one both run in
    Starlette's threadpool, so two concurrent first-requests would otherwise each
    build an index and one would be thrown away.

    Both callers reach this from a worker thread, never from the event loop, so
    blocking here is safe. That is load-bearing for the streaming endpoint: see
    the note in ``chat_stream``.
    """
    engine = app.state.engine
    if engine is not None:
        return engine
    with _engine_lock:
        if app.state.engine is None:
            app.state.engine = ChatEngine(use_judge=True)
        return app.state.engine


# Sourced from profile.json rather than hardcoded: the title surfaces in the
# OpenAPI schema, so a fork would otherwise ship someone else's name there.
#
# The interactive docs are switched off in the deployed app. This is a four-route
# private site, not an API anyone integrates against, so /docs offers a visitor
# nothing while publishing the request shapes and the owner's full legal name in
# the schema title. Set all three to None together: /docs and /redoc are only
# renderers over /openapi.json, so leaving the schema up undoes the other two.
app = FastAPI(
    title=f"{load_profile()['name']} — Resume Chatbot",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Content-Security-Policy. Everything the page loads is same-origin (no CDN, no
# webfont host, no analytics), so 'self' costs nothing here and shuts the door on
# injected third-party script.
#
# Scripts are nonce-gated: the two inline blocks in the template get the
# per-request nonce, and nothing else executes. Styles cannot be, because the
# template writes --tint on elements and the generated career arc carries a
# style attribute on 64 of its marks; a nonce does not apply to style
# attributes, so 'unsafe-inline' is the only option short of rewriting both to
# emit classes. That is a real weakening, but style-only injection with
# script-src locked down is a defacement risk, not an execution one.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """Reject an oversized request body before anything parses it.

    ``max_question_chars`` is enforced in ``_check_request``, which does not run
    until FastAPI has already parsed the JSON into a ChatRequest. That ordering
    means the length cap costs a full read and deserialize of whatever was sent
    before it can reject it, so on its own it is a validation rule rather than a
    defence.

    Content-Length is client-supplied and a client can lie or omit it, so this is
    not a hard guarantee; it is the cheap check that catches the ordinary case
    without reading a byte of the body. A request that omits the header falls
    through to the platform's own limit (32 MB on Cloud Run), which is the real
    backstop.
    """
    if request.method == "POST":
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.max_body_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body is too large."},
            )
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Attach the CSP nonce to the request, then set the response headers.

    The nonce is minted here rather than in the index handler because the header
    and the template have to carry the same value, and only the middleware sees
    both sides of the response.
    """
    nonce = secrets.token_urlsafe(16)
    request.state.csp_nonce = nonce

    response = await call_next(request)

    response.headers["Content-Security-Policy"] = _CSP.format(nonce=nonce)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # frame-ancestors above is the modern control; this covers older browsers
    # that only understand the legacy header.
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    # HSTS only on a request that actually arrived over TLS. Sent over plain http
    # it is ignored by browsers anyway, but emitting it in local development is a
    # good way to poison localhost in your own browser for the next year.
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    if forwarded_proto == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]


# HEAD as well as GET: uptime monitors commonly ping with HEAD to avoid pulling a
# body, and a GET-only route answers those with 405. The Space would then look
# hard-down to the very thing whose job is to keep it awake. Starlette discards
# the body for a HEAD itself, so one handler covers both.
@app.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


# Upstream throttling and a genuine outage used to render as the same sentence,
# which made the site's most likely failure indistinguishable from its worst one.
# On a free Groq tier the token budget is the binding constraint, so "busy, try
# again" is both true and actionable where "unavailable" is neither.
#
# Detected by duck typing rather than `except groq.RateLimitError`: groq is a
# transitive dependency here (langchain-groq owns it, and it is pinned in
# requirements.lock but absent from requirements.txt). Importing it directly
# would make it a direct dependency, and per CLAUDE.md that means a
# requirements.txt entry plus a lockfile regenerated in a clean linux/amd64
# container, which is a heavy price for one isinstance check.
_THROTTLE_MESSAGE = "The assistant is busy right now. Please try again in a minute."
_FAILURE_MESSAGE = "The assistant is unavailable right now."

# Groq signals a spent minute and a spent day the same way, as a 429, so both used
# to land on the sentence above. For a per-minute throttle it is true and
# actionable. For the day's budget it is neither: nothing is different in a
# moment, and a visitor told to wait who comes back to the same sentence has now
# watched the centerpiece of this site fail twice. That is the likelier of the two
# on a free tier, where the real ceiling is roughly thirty questions a day.
#
# The rest of the page does not need Groq, so the message says so. It is the only
# thing a visitor can act on once the day is gone.
_DAILY_MESSAGE = (
    "The assistant has used up today's question budget and will be back tomorrow. "
    "The rest of the page, including the résumé below, still works."
)

# The site's own ceilings, refused before Groq is called. The global day reuses
# the sentence above, since to a visitor it is the same event: the day is gone.
# One client's share is said separately, because for that visitor it is gone
# while the chat still works for everyone else. Fixed sentences, with no count in
# them, so a refusal says nothing about how the limits are tuned.
_CLIENT_DAILY_MESSAGE = (
    "You have reached today's limit of questions. The assistant will answer again "
    "tomorrow, and the rest of the page, including the résumé below, still works."
)
_HOURLY_MESSAGE = "The assistant has hit its usage limit for now. Please try again later."

# The distinction travels only in the body of the 429: the response headers report
# the per-minute token window and refill every minute regardless of how exhausted
# the day is, so reading them would call a spent account healthy. Groq names the
# window it enforced in the message, "tokens per day (TPD)" against "tokens per
# minute (TPM)", which is what these match.
#
# Matching the day rather than the minute is what makes it fail safe. An
# unrecognised body, a reworded message, or a throttle from somewhere else falls
# through to _THROTTLE_MESSAGE, which is the behaviour this already had.
_DAILY_MARKERS = ("per day", "tpd", "rpd")

# Named rather than vague, because the only people who can see it are the ones who
# can fix it: whoever forked this and has not set a key yet, and the owner of a
# deploy whose secret failed to mount. "Unavailable" would send both of them
# looking for an outage. The variable name is not a secret; the README names it on
# the third line of the setup steps.
_UNCONFIGURED_MESSAGE = "The assistant is not configured: set GROQ_API_KEY."


def _is_upstream_throttle(exc: BaseException) -> bool:
    """True when the model provider rate limited us rather than actually failing."""
    if getattr(exc, "status_code", None) == 429:
        return True
    text = str(exc).lower()
    return "rate_limit" in text or "rate limit" in text


def _throttle_message(exc: BaseException) -> str:
    """Which of the two throttle sentences this exception has earned.

    Only called once ``_is_upstream_throttle`` has said yes, so the question here
    is narrower: was it the minute or the day.
    """
    text = str(exc).lower()
    if any(marker in text for marker in _DAILY_MARKERS):
        return _DAILY_MESSAGE
    return _THROTTLE_MESSAGE


def _check_request(req: ChatRequest, request: Request) -> str:
    """Rate limit and validate. Returns the cleaned question or raises HTTPException.

    Shared by the blocking and streaming endpoints so the guardrails can never
    drift apart between them.
    """
    _sweep(time.monotonic())

    # First, and deliberately ahead of both limiters. This is the one check here
    # that is not about the client: the request is fine, the server is not
    # configured. Charging it to the caller's per-IP window, or to the day's
    # budget, would punish a visitor for a deployment mistake and would let a
    # keyless server exhaust its own ceiling answering nothing. It is also free,
    # where every check below it costs either state or a decision.
    if not settings.groq_api_key:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_MESSAGE)

    client = client_ip(request)
    if _rate_limited(client):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")

    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty.")
    if len(question) > settings.max_question_chars:
        raise HTTPException(
            status_code=413,
            detail=f"Question is too long (max {settings.max_question_chars} characters).",
        )

    # Both daily allowances last, so a malformed or oversized request is rejected
    # without spending the client's share or the day's budget on it.
    refusal = _spend(client)
    if refusal:
        raise HTTPException(status_code=429, detail=refusal)
    return question


# Deliberately `def`, not `async def`. The pipeline underneath is synchronous
# throughout: two blocking Groq calls and a FAISS search. In an async handler
# that work runs directly on the event loop and stalls every other request in
# the process for its whole duration, including /health, which turns an ordinary
# slow answer into an uptime alert. Starlette runs a sync handler in a worker
# thread instead, which is what this wants.
#
# The streaming endpoint below stays async: its blocking work lives in a sync
# generator, and Starlette already iterates those in a threadpool.
@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    question = _check_request(req, request)

    try:
        result = get_engine(request.app).answer(question)
    except Exception as exc:  # noqa: BLE001 - surface a clean error, log server-side
        if _is_upstream_throttle(exc):
            # Not an exception-worthy event: the provider is telling us to wait.
            # Logged at warning so a throttled day is legible in the logs without
            # a stack trace per question.
            logging.warning("chat pipeline throttled upstream: %s", exc)
            raise HTTPException(status_code=429, detail=_throttle_message(exc))
        logging.exception("chat pipeline failed")
        raise HTTPException(status_code=502, detail=_FAILURE_MESSAGE)

    return ChatResponse(answer=result.text, sources=result.sources)


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """The same pipeline as /api/chat, delivered as server-sent events.

    Each line is ``data: {"type": ..., ...}``. Types are "sources", "token",
    "done", and "error". Errors are delivered as an event rather than a status
    code, because by the time the pipeline can fail the response has already
    started and the status line is long gone.
    """
    question = _check_request(req, request)
    app_ref = request.app

    def events():
        # get_engine is resolved HERE, inside the sync generator, not in the
        # async handler above. On the first request of a cold container it builds
        # the index, and doing that in the async body would run several seconds of
        # blocking work directly on the event loop, stalling every other request
        # in the process including /health. Starlette iterates a sync generator in
        # a threadpool, which is exactly where this belongs.
        try:
            engine = get_engine(app_ref)
            for kind, payload in engine.stream_answer(question):
                if kind == "sources":
                    body = {"type": "sources", "sources": payload}
                elif kind == "token":
                    body = {"type": "token", "text": payload}
                else:
                    body = {"type": "done"}
                yield f"data: {json.dumps(body)}\n\n"
        except Exception as exc:  # noqa: BLE001 - surface a clean error, log server-side
            # The status line is long gone by now, so the distinction has to
            # travel in the event body. The client renders `detail` either way.
            if _is_upstream_throttle(exc):
                logging.warning("chat stream throttled upstream: %s", exc)
                detail = _throttle_message(exc)
            else:
                logging.exception("chat stream failed")
                detail = _FAILURE_MESSAGE
            error = {"type": "error", "detail": detail}
            yield f"data: {json.dumps(error)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Managed platforms front the container with a reverse proxy that
            # will happily buffer the whole response and defeat the point of
            # streaming. Cloud Run's edge is the one this deployment sits
            # behind; the header is harmless where nothing reads it.
            "X-Accel-Buffering": "no",
        },
    )


class ContactRequest(BaseModel):
    """Plain strings with defaults and no constraints, on purpose.

    A Pydantic validation failure answers with a 422 whose body quotes the
    offending input back, which is the one thing this endpoint promises never to
    do with a visitor's details. Every rule lives in ``contact.clean`` instead,
    whose errors are fixed sentences, and what can still fail validation (a
    wrong type, a body that is not an object) is answered by
    ``contact_validation_error`` with one fixed sentence.

    ``website`` is a honeypot. The form hides it from people and from assistive
    tech; a bot filling every field fills it too.
    """

    name: str = ""
    email: str = ""
    phone: str = ""
    message: str = ""
    website: str = ""


_CONTACT_UNCONFIGURED = "The contact form is not configured: set RESEND_API_KEY and CONTACT_TO."
_CONTACT_FAILURE = "Your message could not be sent right now. Please try again later."
_CONTACT_UNREADABLE = "That message could not be read. Please check each field and try again."


@app.exception_handler(RequestValidationError)
async def contact_validation_error(request: Request, exc: RequestValidationError):
    """A malformed contact body gets a fixed sentence; every other route keeps FastAPI's 422.

    The defaults on ContactRequest stop a *missing* field from failing, but a
    wrong type, a body that is not an object, or broken JSON still fails
    validation, and FastAPI's 422 quotes the input back, field values included.
    That is the one thing this endpoint promises never to do with a visitor's
    details, so its refusal names nothing that was sent.
    """
    if request.url.path == "/api/contact":
        return JSONResponse(status_code=400, content={"detail": _CONTACT_UNREADABLE})
    return await request_validation_exception_handler(request, exc)


def _contact_rate_limited(client: str) -> bool:
    with _limiter_lock:
        now = time.monotonic()
        window = _contact_log.get(client)
        if window is None:
            window = _contact_log[client] = deque()
        _prune(window, now, 3600.0)
        if len(window) >= settings.contact_limit_per_hour:
            return True
        window.append(now)
        return False


def _contact_over_budget() -> bool:
    with _limiter_lock:
        now = time.monotonic()
        _prune(_contact_day, now, 86400.0)
        if len(_contact_day) >= settings.contact_limit_per_day:
            return True
        _contact_day.append(now)
        return False


# `def`, not `async def`: the relay is a blocking HTTPS call to Resend, and on the
# event loop it would stall every other request, the same reasoning as /api/chat.
#
# Its own guard rather than `_check_request`, which is the chat's and opens with a
# Groq key check this route has nothing to do with. The shape is the same: the
# configuration check first because it is about the server, then the per-IP
# window, then validation, and the day's ceiling last so a malformed message
# never spends it.
@app.post("/api/contact")
def contact_owner(req: ContactRequest, request: Request) -> dict[str, bool]:
    _sweep(time.monotonic())

    if not contact.is_configured():
        raise HTTPException(status_code=503, detail=_CONTACT_UNCONFIGURED)

    if _contact_rate_limited(client_ip(request)):
        raise HTTPException(
            status_code=429,
            detail="You have sent several messages already. Please try again in an hour.",
        )

    # A bot is told it succeeded. Refusing would teach it which field to skip.
    if req.website.strip():
        return {"ok": True}

    try:
        message = contact.clean(req.name, req.email, req.phone, req.message)
    except contact.ContactError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if _contact_over_budget():
        raise HTTPException(status_code=429, detail=_CONTACT_FAILURE)

    try:
        contact.send(message)
    except contact.ContactSendError:
        raise HTTPException(status_code=502, detail=_CONTACT_FAILURE)
    return {"ok": True}


def _asset_version() -> str:
    """Cache-busting token for the static assets, from their newest mtime.

    StaticFiles sends last-modified and an etag but no Cache-Control, so a
    browser falls back to heuristic caching and may reuse a stale styles.css
    without revalidating. That is not cosmetic here: the career arc SVG carries
    no colors of its own by design, so a stale stylesheet renders every chord as
    a solid black fill. Changing the query string changes the URL, which skips
    the heuristic entirely.

    Recomputed per request (three stat calls) so an edit shows up on reload.
    """
    newest = 0.0
    for name in ("styles.css", "app.js", "masthead3d.js"):
        path = settings.static_dir / name
        if path.exists():
            newest = max(newest, path.stat().st_mtime)
    return str(int(newest))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    # `?ask=<slug>` opens the page with a question already in the composer, so a
    # cover letter can link straight at the thing it wants read. Resolved here
    # rather than in app.js so it also works with scripting off, and so the
    # question is typed but never sent: see `ask_links` in app/profile.py.
    #
    # The slug is a *key lookup*, never the text itself. An unknown slug yields
    # an empty composer, which means the query string cannot put attacker-chosen
    # text on the page under the owner's name and byline. Jinja would escape it
    # either way; this makes the reflection impossible rather than harmless.
    profile = load_profile()
    prefill = profile["ask_links"].get(request.query_params.get("ask", ""), "")

    # The origin this response is actually served from, which is where the
    # social card's image has to be fetched from. Not `site_url`: that may be a
    # redirect, which hosts no files (docs/deployment.md). Taken from the
    # request so it needs no config and cannot go stale when the Cloud Run
    # hostname changes, which is the failure the Pages indirection exists for.
    origin = request_origin(request)
    card_image = f"{origin}{profile['photo']}" if profile["photo"] else ""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "profile": profile,
            "prefill": prefill,
            "resume_html": load_resume_html(),
            "career_arc_svg": load_career_arc_svg(),
            "results_chart_svg": load_results_chart_svg(),
            "eval_panel_svg": load_eval_panel_svg(),
            "eval_panel_data": load_eval_panel_data(),
            # Pre-serialized rather than a dict the template walks: the JSON-LD
            # block is one `| safe` string, and building it in Python is what
            # keeps its escaping correct. See person_ld_json.
            "person_ld": person_ld_json(origin),
            "card_image": card_image,
            "starter_answers": starter_answers_json(),
            "asset_v": _asset_version(),
            # Read from settings rather than written into the template. The
            # footer named Llama 3.3 for nine days after that model was retired
            # and the pipeline moved to gpt-oss-120b, because a hardcoded string
            # has nothing telling it to change. The vendor prefix is dropped
            # because "openai/gpt-oss-120b" running on Groq reads as an error.
            "model_name": settings.drafter_model.split("/")[-1],
            # The form is left out of the page entirely rather than shown and
            # 503ing, which is the state of any fork without a Resend key.
            "contact_enabled": contact.is_configured(),
            # Must match the nonce the middleware puts in the CSP header for this
            # same response, or the inline theme script is blocked and the page
            # flashes the wrong background on every load.
            "csp_nonce": request.state.csp_nonce,
        },
    )


# Serve css/js/assets under /static.
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
