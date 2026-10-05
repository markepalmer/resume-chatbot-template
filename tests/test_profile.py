"""profile.json against app/profile.py's defaults, and the rendered resume."""

import json
import re
from pathlib import Path

from app.profile import PROFILE_PATH, _DEFAULTS, load_profile, load_resume_html


def _raw_profile() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def test_every_profile_key_has_a_default():
    """CLAUDE.md: `_DEFAULTS` must have an entry for every top-level key.

    This is the highest-value test in the suite because the failure is silent.
    A key present in neither profile.json nor `_DEFAULTS` does not raise:
    Jinja's dot access returns Undefined, which is falsy, so the `{% if %}`
    guarding that section is skipped and the section vanishes from the page
    with nothing in the logs.

    Keys starting with an underscore are excluded by convention. They are
    parked data (`_disabled_results`) that no template reads.
    """
    keys = {k for k in _raw_profile() if not k.startswith("_")}
    missing = sorted(keys - set(_DEFAULTS))
    assert not missing, (
        "CLAUDE.md convention: app/profile.py's _DEFAULTS must have an entry for "
        f"every top-level profile.json key. Missing: {missing}. Without a default, "
        "the page section these drive disappears silently instead of showing a "
        "placeholder."
    )


def test_profile_loads_and_fills_known_as():
    profile = load_profile()
    assert profile["name"], "profile.json must carry a name; it titles the OpenAPI schema too"
    assert profile["known_as"], (
        "known_as must resolve: load_profile falls back to the first word of name, "
        "so an empty value here means name was empty as well"
    )
    for key in _DEFAULTS:
        assert key in profile, f"load_profile dropped the defaulted key {key!r}"


def test_resume_html_renders_non_empty():
    html = load_resume_html()
    assert html.strip(), (
        "knowledge/resume.md rendered to nothing. The Resume section on the page "
        "is this string, so an empty render is an empty section."
    )
    assert "<h2" in html or "<h3" in html, (
        "resume HTML has no headings, which means the markdown render collapsed "
        "the document structure"
    )
    assert "# " not in html, "raw markdown leaked into the rendered resume HTML"
    assert "**Source:" not in html, (
        "load_resume_html must strip the internal 'Source:' retrieval annotation; "
        "it is a retrieval tag, not resume content"
    )


def _registry_form_names() -> tuple[set[str], set[str]]:
    """The form registry in static/masthead3d.js, as (header forms, panel forms).

    Read out of the JS rather than duplicated, because a duplicate is the thing
    being tested. The parse is deliberately shallow: it wants the keys of the
    FORMS object literal and whether each carries `panel: true`, which is one
    regex over lines that start an entry, not a JS parser.
    """
    source = (Path(__file__).resolve().parents[1] / "static" / "masthead3d.js").read_text(
        encoding="utf-8"
    )
    body = source.split("const FORMS = {", 1)
    assert len(body) == 2, "static/masthead3d.js no longer declares `const FORMS = {`"
    header: set[str] = set()
    panel: set[str] = set()
    for line in body[1].splitlines():
        match = re.match(r"\s{2}(\w+): \{(.*)\},$", line)
        if not match:
            if line.startswith("};"):
                break
            continue
        (panel if "panel: true" in match.group(2) else header).add(match.group(1))
    assert header, "parsed no forms out of the FORMS registry; the entry shape changed"
    return header, panel


def test_setup_menu_matches_the_form_registry():
    """setup_profile.py's shape menu and masthead3d.js's registry name the same forms.

    Two lists, two languages, one set of names. The registry drops a name it does
    not know and falls back to its own cycle, so drift here does not break the
    header: it silently costs a forker the shape they picked, or hides a shape
    they could have had. Neither shows up anywhere.
    """
    from scripts.setup_profile import DEFAULT_MASTHEAD_FORMS, MASTHEAD_FORMS

    header, panel = _registry_form_names()
    offered = set(MASTHEAD_FORMS)
    assert offered - header == set(), (
        f"setup offers shapes the registry does not build: {sorted(offered - header)}. "
        "A name masthead3d.js does not know is dropped in the browser, so the "
        "forker gets the default cycle and no explanation."
    )
    assert header - offered == set(), (
        f"the registry builds shapes setup never offers: {sorted(header - offered)}. "
        "Add them to MASTHEAD_FORMS with a one-line description, or mark them "
        "`panel: true` if they are not header forms."
    )
    assert panel.isdisjoint(offered), (
        f"setup offers a panel-only form: {sorted(panel & offered)}. Those are echoes "
        "of the figure they sit behind and mean nothing in the header."
    )
    assert set(DEFAULT_MASTHEAD_FORMS) <= offered
    assert DEFAULT_MASTHEAD_FORMS == _DEFAULTS["masthead_forms"], (
        "setup's default cycle and app/profile.py's disagree, so a fresh fork's "
        "page and its setup run would propose different headers"
    )
