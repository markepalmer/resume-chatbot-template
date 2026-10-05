"""The social card's image URL, and the scheme it goes out with.

`og:image` is built from the request's own origin rather than from `site_url`,
because `site_url` is the Pages redirect and hosts no files. Behind Cloud Run
that origin arrives over plain HTTP, since TLS is terminated at the front end,
so the tag went out as `http://...` on a page whose canonical URL is `https://`.
Most scrapers cache the first failure, which makes a wrong card expensive to
notice and expensive to correct.
"""

import re

import pytest

from app.profile import load_profile

CARD = re.compile(r'<meta property="og:image" content="([^"]*)"')

# The whole card block in templates/index.html is gated on `site_url`, and an
# empty one is a supported state rather than a misconfiguration: a fork with no
# public address yet emits no card at all instead of a card full of relative
# URLs that resolve against nothing. So these tests have nothing to assert
# about until an address is set, and skipping says that, where failing would
# hand every fresh fork four red tests describing a feature it has not reached.
pytestmark = pytest.mark.skipif(
    not load_profile()["site_url"],
    reason="no site_url set, so the page emits no social card to check",
)


def og_image(client, **kwargs):
    body = client.get("/", **kwargs).text
    found = CARD.search(body)
    assert found, "og:image is missing from the page"
    return found.group(1)


def test_forwarded_proto_decides_the_scheme(client):
    """What Cloud Run actually sends: one hop, https at the front end."""
    url = og_image(client, headers={"x-forwarded-proto": "https"})
    assert url.startswith("https://"), url


def test_the_leftmost_hop_wins(client):
    """Several proxies deep, the first entry is the scheme the client used."""
    url = og_image(client, headers={"x-forwarded-proto": "https, http"})
    assert url.startswith("https://"), url


def test_no_header_leaves_the_request_scheme_alone(client):
    """Local development over plain HTTP, and anything that is not a proxy."""
    assert og_image(client).startswith("http://")


def test_a_junk_header_is_ignored_rather_than_interpolated(client):
    """The value reaches a URL, so it is matched against a list, never trusted."""
    url = og_image(client, headers={"x-forwarded-proto": "javascript:alert(1)"})
    assert url.startswith("http://"), url


def test_the_image_is_served_from_the_same_origin(client):
    """A card image is fetched with no page to resolve a relative path against."""
    url = og_image(client, headers={"x-forwarded-proto": "https"})
    path = "/" + url.split("://", 1)[1].split("/", 1)[1]
    assert client.get(path).status_code == 200, path
