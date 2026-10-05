"""client_ip: which bucket a request is counted in.

IPv4 is the address. IPv6 is the /64, because one subscriber is routinely given
a whole /64 and can rotate through it, so keying on the full address would give
one person 2**64 fresh buckets for every per-client limit. Addresses are from the
documentation ranges (RFC 5737, RFC 3849).
"""

import pytest
from starlette.requests import Request

from app import main
from app.config import settings


def _request(forwarded: str | None = None, peer: str = "testclient") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_ipv4_is_unchanged():
    assert main.client_ip(_request("203.0.113.7")) == "203.0.113.7"


def test_two_addresses_in_one_slash_64_share_a_bucket():
    a = main.client_ip(_request("2001:db8:1:2::1"))
    b = main.client_ip(_request("2001:db8:1:2:ffff:ffff:ffff:9"))
    assert a == b == "2001:db8:1:2::/64"


def test_neighbouring_slash_64s_are_separate():
    assert main.client_ip(_request("2001:db8:1:2::1")) != main.client_ip(_request("2001:db8:1:3::1"))


def test_an_ipv4_mapped_address_is_its_ipv4():
    assert main.client_ip(_request("::ffff:203.0.113.7")) == "203.0.113.7"


def test_the_socket_peer_is_bucketed_too(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_hops", 0)
    assert main.client_ip(_request(peer="2001:db8:1:2::5")) == "2001:db8:1:2::/64"


@pytest.mark.parametrize("value", ["testclient", "unknown", "not an address"])
def test_an_unparseable_key_is_kept_as_it_is(value):
    assert main._bucket(value) == value


def test_rotating_within_a_slash_64_does_not_escape_the_minute_limit(client, no_engine):
    """End to end: each request from a fresh address in one /64 counts against one window."""
    payload = {"question": "x" * (settings.max_question_chars + 1)}
    codes = [
        client.post("/api/chat", json=payload,
                    headers={"x-forwarded-for": f"2001:db8:1:2::{i + 1:x}"}).status_code
        for i in range(settings.rate_limit_per_minute + 1)
    ]
    assert codes[-1] == 429, "a client rotating through its own /64 got a fresh bucket each time"
    assert all(code != 429 for code in codes[:-1])
