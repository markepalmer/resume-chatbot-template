"""Load the site owner's identity from profile.json.

This is the single file a forker edits to make the site their own (alongside the
knowledge/ documents). Everything the frontend and the prompts need about *who*
the site is for comes from here, so no personal details are hardcoded in the app.
"""

import json
from functools import lru_cache

import markdown

from .config import PROFILE_PATH, ROOT_DIR
from .fingerprint import corpus_fingerprint

RESUME_PATH = ROOT_DIR / "knowledge" / "resume.md"
CAREER_ARC_PATH = ROOT_DIR / "static" / "career_arc.svg"
RESULTS_CHART_PATH = ROOT_DIR / "static" / "results_chart.svg"
EVAL_PANEL_PATH = ROOT_DIR / "static" / "eval_panel.svg"
EVAL_PANEL_DATA_PATH = ROOT_DIR / "static" / "eval_panel.json"
STARTER_ANSWERS_PATH = ROOT_DIR / "static" / "starter_answers.json"

_DEFAULTS = {
    "name": "Your Name",
    "known_as": "",
    "role": "Your Role",
    "photo": "",
    "tagline": "A short tagline about you.",
    # The masthead's three rows: a small-caps credential line, a list of the
    # things you have shipped, and one closing sentence. Empty by default and
    # the template falls back to rendering `tagline` as a paragraph, so a fork
    # that never fills this in still gets a header rather than a gap. `tagline`
    # is not redundant either way: it is the page's <meta description> and the
    # first-person quote setup_profile.py writes into the corpus.
    "bio": {},
    "email": "you@example.com",
    "location": "",
    # The résumé file the Download button serves, as a site path. Empty is the
    # forkable state and not a gap: the template falls back to the browser's
    # print-to-PDF of the rendered résumé, so a fork with no file still has a
    # working button.
    "resume_pdf": "",
    # The public origin this page is served from, used only to make the social
    # card's URLs absolute: og:image and og:url are fetched by a scraper that
    # has no page context, so a relative path there resolves against nothing and
    # the preview silently falls back to a bare link. Empty is a valid state and
    # the template then omits the card entirely rather than emitting a broken
    # one. This is the Pages origin, not the run.app one, for the reason in
    # docs/deployment.md: the run.app hostname is derived from project number
    # and region and so changes out from under any link that hardcodes it.
    "site_url": "",
    # Slug -> question, read by the `?ask=` deep link. A cover letter links to
    # `/?ask=scheduler` and the page opens with that question already typed into the
    # composer, scrolled into view, and NOT sent: the visitor still presses Ask.
    # Deliberate. Sending on load would let any crawler or repeated open spend
    # the day's Groq budget, which is roughly 33 questions in total, and it
    # would also put an answer on screen that nobody asked for.
    "ask_links": {},
    "links": [],
    "highlights": [],
    # Legacy form of the stat band: a list of its own. A highlight card may now
    # carry a `stat` instead, and when any does, the band is built from the
    # cards (see stats_from). Kept as a key so a fork that still uses the flat
    # list gets its band rather than a silent gap.
    "stats": [],
    "starter_questions": [],
    "skills": {"Core Skills": ["Teamwork", "Leadership", "Communication", "Punctual"]},
    "career": [],
    "results": {},
    # Sections of individual knowledge/ documents injected on EVERY call, as
    # [source, heading] pairs, even though the rest of that document is not.
    # This lives here rather than in app/retriever.py because the filenames are
    # the owner's corpus, not the application's: a fork whose confidential work
    # is in a differently named file should not have to edit Python to protect
    # it. Empty is a valid answer and means the corpus has no such section.
    "always_on_sections": [],
    # Corpus files that are confidential end to end. Distinct from
    # always_on_sections, which names files that merely CONTAIN a
    # Confidentiality section. A file can easily be one and not the other: a
    # mostly public document with one withheld section belongs in that list and
    # not this one, and conflating them quietly widens an end-to-end rule over
    # a document that was never meant to carry it.
    "confidential_files": [],
    # A regex, matched against every knowledge/ heading, marking sections that
    # are confidential even in files that are otherwise public. Empty disables
    # the scan, which is the right answer for a corpus with no such sections.
    "confidential_heading_pattern": "",
    # The masthead cloud's cycle of shapes, in order. Names come from the form
    # registry in static/masthead3d.js, which drops any it does not know and
    # falls back to its own default, so a typo here costs the chosen cycle and
    # not the header. Three is the shipped length: the wait for any one shape is
    # a whole cycle, and at 10s a form that is 30s already.
    "masthead_forms": ["horizon", "wave", "torus"],
}


@lru_cache(maxsize=1)
def load_profile() -> dict:
    """Read profile.json, filling in defaults for any missing keys.

    ``known_as`` is the informal name used in the chat UI and prompts (for example
    "Ada" when the formal name is "Ada P. Okonkwo"). It falls back to the first
    word of ``name`` when not set.
    """
    data: dict = {}
    if PROFILE_PATH.exists():
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    profile = {**_DEFAULTS, **data}
    if not profile["known_as"]:
        profile["known_as"] = profile["name"].split(" ")[0]
    profile["stats"] = stats_from(profile["highlights"], profile["stats"])
    return profile


def stats_from(highlights: list, legacy: list) -> list:
    """Build the stat band from the highlight cards, or fall back to the flat list.

    A stat is a number about one of the cards, so it lives on that card as
    ``stat`` and the band follows the carousel: same order, and ``tint`` is the
    card's index so the tile and the card share a color. Reordering the cards
    reorders the band, and the two cannot disagree about which number belongs
    to which story. A card without a stat simply has no tile.

    The flat ``stats`` list is honored only when no card carries a stat, with
    tints by position, which is what it always was.
    """
    tied = [
        {**card["stat"], "tint": i}
        for i, card in enumerate(highlights)
        if isinstance(card, dict) and card.get("stat")
    ]
    if tied:
        return tied
    return [{**s, "tint": i} for i, s in enumerate(legacy)]


# Keyed by origin, so a service reachable at both of its hostnames does not
# thrash a single-entry cache. Small and bounded: the set of origins that can
# reach this handler is the deployment's own.
@lru_cache(maxsize=4)
def person_ld_json(origin: str = "") -> str:
    """Schema.org ``Person`` for the page's JSON-LD block, as a JSON string.

    ``origin`` is the scheme and host this response is actually being served
    from, and it is what ``image`` is built against. It is deliberately not
    ``site_url``: that is the GitHub Pages address, which is a static redirect
    and serves no files of its own, so an image URL under it is a guaranteed
    404 (docs/deployment.md). ``url`` stays ``site_url``, because that is the
    stable public address a card should point a reader at, and the one already
    printed in sent applications.

    Built here rather than spelled out in the template for two reasons. It is
    identity, which CLAUDE.md keeps out of templates/ entirely; and hand-writing
    JSON in Jinja means hand-escaping it, where one apostrophe in a tagline
    produces a block that every consumer silently discards as malformed.

    ``json.dumps`` does not escape a forward slash, so a ``</script>`` appearing
    inside any profile string would close this block early and everything after
    it would parse as markup. Escaping the sequence is what makes that inert.
    Only the keys with a value are emitted: an empty string in structured data
    is a claim that the field is blank, which is worse than its absence.
    """
    profile = load_profile()
    site = profile["site_url"].rstrip("/")
    person = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": profile["name"],
        "url": f"{site}/" if site else "",
        "jobTitle": profile["role"],
        "description": profile["tagline"],
        "image": f"{origin.rstrip('/')}{profile['photo']}" if origin and profile["photo"] else "",
        "address": profile["location"],
        "email": f"mailto:{profile['email']}" if profile["email"] else "",
        "sameAs": [link["url"] for link in profile["links"] if link.get("url")],
    }
    person = {k: v for k, v in person.items() if v}
    return json.dumps(person, indent=2).replace("</", "<\\/")


@lru_cache(maxsize=1)
def load_resume_html() -> str:
    """Render knowledge/resume.md into HTML for the collapsible résumé section.

    Uses the same file the retrieval index reads, so the printed résumé and the
    chatbot never drift apart. The top-level title and the internal "Source:" tag
    are dropped: the section supplies its own heading, and the tag is a retrieval
    annotation, not resume content.
    """
    if not RESUME_PATH.exists():
        return ""
    lines = RESUME_PATH.read_text(encoding="utf-8").splitlines()
    kept = [
        line
        for line in lines
        if not line.startswith("# ") and not line.strip().startswith("**Source:")
    ]
    return markdown.markdown("\n".join(kept), extensions=["extra"])


@lru_cache(maxsize=1)
def load_career_arc_svg() -> str:
    """Read the pre-rendered Career Arc diagram for inlining into the page.

    The file is a build artifact of ``tools/build_career_arc.py``, not something
    the app generates on request: the layout only changes when profile.json's
    ``career`` block does. It is inlined into the document rather than served to
    an <img> because an external SVG cannot see the page's stylesheet, so it
    could not read --c1..--c6 and would ignore the theme toggle.

    Returns "" when the artifact is missing, which hides the section rather than
    breaking the page. A fresh fork with no ``career`` block never builds one.
    """
    if not CAREER_ARC_PATH.exists():
        return ""
    return CAREER_ARC_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_results_chart_svg() -> str:
    """Read the pre-rendered Results chart for inlining into the page.

    Same contract as the career arc: a build artifact of
    ``tools/build_results_chart.py``, inlined so it can read the theme
    variables, and "" when absent so the section disappears instead of the page
    breaking. A fork with no ``results`` block never builds one.
    """
    if not RESULTS_CHART_PATH.exists():
        return ""
    return RESULTS_CHART_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_eval_panel_svg() -> str:
    """Read the pre-rendered Eval panel for inlining into the page.

    Same contract as the other two figures: a build artifact of
    ``tools/build_eval_panel.py``, inlined so it can read the theme variables,
    and "" when absent so the section disappears instead of the page breaking.

    It differs from them in what it is built from. The arc and the chart read
    ``profile.json``, so any fork that fills in its own details gets its own
    figure. This one reads run files under ``evals/results/``, which are this
    project's measurements and nobody else's, so a fork inherits an empty
    section rather than a wrong one until it has run the harness itself.
    """
    if not EVAL_PANEL_PATH.exists():
        return ""
    return EVAL_PANEL_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_eval_panel_data() -> dict:
    """Read the Eval panel's table data, the accessible equal of the figure.

    Written by the same generator as the SVG, so the table under the figure and
    the figure itself cannot disagree. Returns {} when absent, which hides the
    section rather than rendering a chart with no readable fallback.

    ``measured_for`` is the guard, and it is why a fork does not inherit this
    section. These numbers are a claim that *this* assistant was measured, and
    the generated pair is committed, so a fork that changed nothing but
    profile.json used to render the previous owner's findings as its own. That
    is worse than a stale figure: the career arc showed someone else's history,
    this shows evidence of work the reader is being asked to credit. Failing
    closed costs the owner one rerun of the generator and costs a fork nothing
    it had a right to. The template guards on rows, so the SVG goes with it.

    Returns {} for every failure: absent, unreadable, malformed, or measured for
    somebody else.
    """
    if not EVAL_PANEL_DATA_PATH.exists():
        return {}
    try:
        data = json.loads(EVAL_PANEL_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("measured_for") != load_profile().get("name"):
        return {}
    return data


@lru_cache(maxsize=1)
def load_starter_answers() -> list[dict]:
    """Pre-answered starter questions, but only while they are still true.

    Written by ``tools/build_starter_answers.py``. Each entry is real pipeline
    output that the judge passed, so serving it breaks no rule: the page still
    shows only checked text. What it buys is the day's token budget, since these
    four questions are the likeliest first click and their answers are the same
    for every visitor.

    The guard is the point of this function. A saved answer is a claim frozen in
    time, and the corpus underneath it moves. If the fingerprint in the file does
    not match the corpus, prompts, and models as they are right now, the whole
    file is dropped and every chip runs live again. Stale answers are not
    filtered one by one, because the fingerprint cannot say *which* answers an
    edit invalidated, only that something changed.

    Returns [] for every failure: absent, unreadable, malformed, or stale. The
    caller then behaves exactly as it did before this feature existed.
    """
    if not STARTER_ANSWERS_PATH.exists():
        return []
    try:
        data = json.loads(STARTER_ANSWERS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    if data.get("fingerprint") != corpus_fingerprint():
        return []
    return [
        entry
        for entry in data.get("answers", [])
        if entry.get("question") and entry.get("answer")
    ]


@lru_cache(maxsize=1)
def starter_answers_json() -> str:
    """The saved starter answers as a JSON string, ready to embed in the page.

    Embedded in the document rather than fetched from ``/static``: the whole
    value of these is that the chip answers with no round trip at all, and four
    answers are a couple of kilobytes against a page already around 60 KB. A
    fetch would trade the feature's only advantage for a rounding error in page
    weight. When there is nothing to serve this is ``[]``, and the client falls
    straight through to asking live.

    Escaping ``</`` is what keeps a ``</script>`` inside any answer from closing
    the block early and spilling the rest of the JSON into the page as markup.
    """
    return json.dumps(load_starter_answers(), ensure_ascii=False).replace("</", "<\\/")
