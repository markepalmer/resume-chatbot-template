"""Every inline <script> on the rendered page carries the CSP nonce.

CLAUDE.md: an inline script without `nonce="{{ csp_nonce }}"` is dropped by
CSP whether or not it executes. The non-executing case is the one worth a test:
`#skills-data` is a `type="application/json"` block that the browser never runs,
which makes it look like it does not need a nonce, and CSP drops it anyway. The
symptom is the skill network rendering empty, with a console error nobody sees.
"""

from html.parser import HTMLParser


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.tags.append(dict(attrs))


def _scripts(html: str) -> list[dict]:
    parser = _Scripts()
    parser.feed(html)
    return parser.tags


def test_every_inline_script_has_a_nonce(client):
    response = client.get("/")
    inline = [s for s in _scripts(response.text) if not s.get("src")]
    assert inline, (
        "the page rendered no inline <script> at all, which means the theme "
        "script and the skills data block are both gone"
    )
    naked = [s for s in inline if not s.get("nonce")]
    assert not naked, (
        "inline <script> without nonce=\"{{ csp_nonce }}\": "
        f"{naked}. CSP drops these, executing or not."
    )


def test_script_nonces_match_the_csp_header(client):
    """The template and the header must carry the same per-request value.

    They are minted in the middleware precisely because only it sees both
    sides. A mismatch blocks the inline theme script, and the page then flashes
    the wrong background on every load.
    """
    response = client.get("/")
    csp = response.headers["Content-Security-Policy"]
    header_nonce = csp.split("'nonce-")[1].split("'")[0]
    for script in _scripts(response.text):
        if script.get("src"):
            continue
        assert script.get("nonce") == header_nonce, (
            f"inline script nonce {script.get('nonce')!r} does not match the CSP "
            f"header's {header_nonce!r}; the browser will block it"
        )


def test_nonce_is_fresh_per_request(client):
    first = client.get("/").headers["Content-Security-Policy"]
    second = client.get("/").headers["Content-Security-Policy"]
    assert first != second, (
        "the CSP nonce is identical across two requests. A reused nonce is not a "
        "nonce: it can be learned once and then satisfied by injected script."
    )


def test_external_scripts_are_same_origin(client):
    """CLAUDE.md: a frontend dependency is vendored, never pulled from a CDN.

    Pointing at a CDN means widening script-src, which hands a third party the
    ability to run script on the page.
    """
    offenders = [
        s["src"]
        for s in _scripts(client.get("/").text)
        if s.get("src") and s["src"].startswith(("http://", "https://", "//"))
    ]
    assert not offenders, (
        f"absolute script src on the page: {offenders}. Vendor it into "
        "static/vendor/ and import it same-origin instead."
    )
