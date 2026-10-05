"""The limiter maps are shared across threads, so every touch of them is locked.

`/api/chat` is `def`, so its guard runs in Starlette's threadpool, and
`/api/chat/stream` runs its guard on the event loop. Both read and write the
same dicts and deques. Unlocked, two things failed: `_sweep` walking a dict
while another thread inserted into it raised "dictionary changed size during
iteration", a 500 on a request that did nothing wrong; and a check-then-append
let N concurrent requests each see room, overshooting a limit by up to the
threadpool width.

The GIL does not prevent either. It makes one bytecode atomic, not a loop, and
the switch interval is shortened here so the window is hit in milliseconds
rather than by luck.
"""

import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import main
from app.config import settings


@pytest.fixture
def fast_switching():
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(previous)


def test_sweep_never_raises_against_concurrent_inserts(reset_limiter_state, fast_switching):
    stop = threading.Event()
    errors: list[BaseException] = []

    def insert(worker: int) -> None:
        n = 0
        while not stop.is_set():
            main._rate_limited(f"198.51.100.{worker}:{n}")
            n += 1

    def sweep() -> None:
        try:
            for _ in range(3000):
                main._last_sweep = 0.0
                main._sweep(main.time.monotonic() + 120.0)
        except RuntimeError as exc:  # dictionary changed size during iteration
            errors.append(exc)

    workers = [threading.Thread(target=insert, args=(w,)) for w in range(4)]
    for t in workers:
        t.start()
    try:
        sweep()
    finally:
        stop.set()
        for t in workers:
            t.join()
    assert not errors, f"_sweep raised under concurrent inserts: {errors[0]!r}"


def test_the_minute_limit_is_exact_under_concurrency(reset_limiter_state, fast_switching):
    """Thirty rounds, because one round catches an unlocked overshoot only sometimes."""
    limit = settings.rate_limit_per_minute
    counts = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for _ in range(30):
            main._request_log.clear()
            counts.append(sum(pool.map(
                lambda _: not main._rate_limited("198.51.100.1"), range(limit * 10))))
    assert set(counts) == {limit}, f"admitted per round {sorted(set(counts))} against a limit of {limit}"


@pytest.mark.parametrize(
    "call",
    [
        lambda: main._rate_limited("198.51.100.2"),
        lambda: main._spend("198.51.100.2"),
        lambda: main._sweep(main.time.monotonic() + 120.0),
        lambda: main._contact_rate_limited("198.51.100.2"),
        lambda: main._contact_over_budget(),
    ],
    ids=["rate_limited", "spend", "sweep", "contact_rate_limited", "contact_over_budget"],
)
def test_every_limiter_waits_for_the_lock(reset_limiter_state, call):
    """Deterministic: while the lock is held, the call cannot finish."""
    done = threading.Event()
    with main._limiter_lock:
        worker = threading.Thread(target=lambda: (call(), done.set()))
        worker.start()
        assert not done.wait(0.2), "the call finished while the limiter lock was held"
    worker.join(timeout=5)
    assert done.is_set()
