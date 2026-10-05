"""The sync/async convention, which is one word wide and has no visible symptom.

CLAUDE.md: a handler whose work is synchronous is declared `def`. The pipeline
underneath is two blocking Groq calls and a FAISS search. Marked `async`, that
work runs directly on the event loop and stalls every other request in the
process for its whole duration, /health included, which turns an ordinary slow
answer into an uptime alert. Starlette runs a `def` handler in a worker thread
instead.

There is nothing to see when this regresses. The endpoint still returns the
right answer; it just takes the rest of the process down with it while it does.
Hence a test rather than a comment, although the comment is there too.
"""

import inspect

import pytest

from app import main


def _endpoint(path: str):
    for route in main.app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"no route registered at {path}")


def test_chat_endpoint_is_sync():
    endpoint = _endpoint("/api/chat")
    assert not inspect.iscoroutinefunction(endpoint), (
        "/api/chat is declared `async def`. Its whole body is blocking: two Groq "
        "calls and a FAISS search, run on the event loop, stalling every other "
        "request in the process including /health. Declare it `def` and let "
        "Starlette put it in a worker thread."
    )


def test_streaming_generator_is_sync():
    """The generator inside chat_stream must be `def`, and get_engine must be in it.

    The handler itself is correctly `async def`: its blocking work lives in a
    sync generator, and Starlette already iterates those in a threadpool. What
    must not move is `get_engine`. Resolved in the async body instead, it builds
    the FAISS index on the event loop on the first request to a cold container.

    Read from the source rather than by calling: `events` is a closure, so there
    is no object to inspect without running the handler, and running it is what
    this suite exists to avoid.
    """
    source = inspect.getsource(_endpoint("/api/chat/stream"))
    assert "async def events" not in source, (
        "the streaming generator is `async def`. Starlette iterates a sync "
        "generator in a threadpool; an async one runs its blocking body on the "
        "event loop."
    )
    assert "def events(" in source, (
        "chat_stream no longer defines a sync `events` generator; re-check where "
        "its blocking work now runs"
    )

    body_before_generator = source.split("def events(")[0]
    assert "get_engine" not in body_before_generator, (
        "get_engine is resolved in the async handler body rather than inside the "
        "sync generator. On a cold container that builds the FAISS index on the "
        "event loop, stalling every concurrent request including /health."
    )


def test_stream_endpoint_is_async():
    assert inspect.iscoroutinefunction(_endpoint("/api/chat/stream")), (
        "/api/chat/stream is expected to stay `async def`: it does no blocking "
        "work itself and returns a StreamingResponse over a sync generator"
    )


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/stream"])
def test_both_chat_endpoints_share_the_guardrails(path):
    """CLAUDE.md: guardrails live in _check_request, called by both endpoints.

    Inlined into one of them, the rate limit, the length cap and the budget
    ceiling drift apart, and the endpoint that fell behind becomes the free one.
    """
    source = inspect.getsource(_endpoint(path))
    assert "_check_request(" in source, (
        f"{path} does not call _check_request. Both chat endpoints must share it "
        "so the rate limit, length cap and budget ceiling cannot diverge."
    )
