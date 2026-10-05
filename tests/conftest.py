"""Shared fixtures.

Two rules hold for the whole suite:

* No network, and no Groq call, ever. Nothing here constructs a ChatEngine.
  The one endpoint that would build one is exercised with ``get_engine``
  monkeypatched out, so the guardrail tests can run without an API key, an
  embedding model, or a FAISS index.
* The app module is imported, and ``TestClient`` is used *without* its context
  manager, so ``lifespan`` does not run. Nothing under test needs what it does
  (it sets ``app.state.engine`` to None and warns on a missing key), and leaving
  it out keeps every test to one code path. ``tests/test_unconfigured.py`` is the
  exception and enters the context manager deliberately, because lifespan's own
  behaviour is what it asserts.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Set before app.config is imported anywhere: pydantic-settings reads the
# environment at class instantiation, and the real .env may or may not exist on
# the machine running the suite. The value is never used to call anything.
import os  # noqa: E402

os.environ.setdefault("GROQ_API_KEY", "test-key-not-used")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture
def profile() -> dict:
    from app.profile import load_profile

    return load_profile()


@pytest.fixture
def client(reset_limiter_state):
    """A TestClient over the real app, with lifespan deliberately not run."""
    from fastapi.testclient import TestClient

    from app import main

    return TestClient(main.app)


@pytest.fixture
def no_engine(monkeypatch):
    """Make reaching the pipeline a test failure rather than a network call.

    Used by any test asserting that a guardrail rejects a request: if the
    guardrail fires, the pipeline is never reached, and if it stops firing this
    turns a silent model download into an immediate failure with a reason.
    """
    from app import main

    def explode(app):
        raise AssertionError(
            "get_engine was called. This test asserts a guardrail rejects the "
            "request before the pipeline is reached; reaching it means the "
            "guardrail did not fire."
        )

    monkeypatch.setattr(main, "get_engine", explode)


@pytest.fixture
def reset_limiter_state():
    """Clear the rate limiter and budget windows around every test.

    They are module-level globals shared by the whole process, so without this
    the request a header test makes is still counted when the rate-limit test
    runs, and test order decides whether the suite passes.
    """
    from app import main

    def clear():
        main._request_log.clear()
        main._request_day.clear()
        main._global_hour.clear()
        main._global_day.clear()
        main._contact_log.clear()
        main._contact_day.clear()
        main._last_sweep = 0.0

    clear()
    yield
    clear()
