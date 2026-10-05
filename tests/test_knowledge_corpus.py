"""Hygiene and privacy rules for the grounding corpus.

Everything under knowledge/ is chunked and indexed, so anything written there
is retrievable and can be quoted back to a visitor. knowledge/resume.md is
additionally rendered on the public page. Both are one-way doors: a figure that
reaches the index has been published.
"""

import json
import re

import pytest

from app.config import PROFILE_PATH, settings

_PROFILE = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# Confidential regions, and why this rule is shaped the way it is.
#
# An earlier version of this file listed the specific withheld figures as
# literals, each with a label naming what it counted. That is a worse
# disclosure than the one it protects against: the corpus says nothing, and the
# test said what the corpus was refusing to say, in a repo profile.json
# advertises as an open source template. A test that scrubs a secret must not
# be the place the secret survives.
#
# So the rule is general and value-free. Inside a confidential region, *no bare
# cardinal figure may appear at all*, with a narrow allowlist for years. That
# publishes nothing, and it catches the next figure someone pastes in, which a
# fixed list of three never could.
#
# It also declines to name the offender. A failure reports file and line and
# stops there, because pytest output lands in CI logs and a message quoting the
# number would re-publish it one level down.
#
# There is deliberately no value-based escape hatch. A legitimate figure that
# needs to sit in a confidential section is rephrased or moved out of it; an
# allowlist of permitted values would reintroduce exactly the leak above.
# ---------------------------------------------------------------------------

# Whole files that are confidential end to end. Read from profile.json rather
# than listed here, so a fork protects its own files without editing a test.
# Deliberately NOT derived from always_on_sections: that names files which
# CONTAIN a Confidentiality section, which is a weaker claim than being
# confidential throughout, and conflating them widens this rule over documents
# that are mostly public.
CONFIDENTIAL_FILES = tuple(_PROFILE.get("confidential_files", []))

# Plus any markdown section, in any corpus file, whose heading names the
# engagement. Heading-scoped rather than line-scoped on purpose:
# roles-open-to.md can name a confidential engagement in a bullet inside a
# general list that also carries unrelated and entirely publishable figures, so
# a rule keyed on "any line naming it" would be both a false positive there and a false
# negative on the line below it.
# Empty means a corpus with no such cross-file sections, which is the common
# case for a fork and is not an error. None disables the heading scan entirely
# rather than compiling an empty pattern, which would match every heading.
_HEADING_PATTERN = _PROFILE.get("confidential_heading_pattern", "")
CONFIDENTIAL_HEADING = re.compile(_HEADING_PATTERN, re.I) if _HEADING_PATTERN else None

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

# A bare cardinal: a digit run that stands as its own token, optionally with
# thousands separators, a decimal part, a currency mark or a magnitude suffix.
# The lookbehind keeps it from firing inside an identifier, a version string or
# a URL path, where the digits are part of a name rather than a count.
_FIGURE = re.compile(
    r"""
    (?<![\w.\-/])              # not glued to a word, dot, hyphen or slash
    \$?                        # optional currency mark
    \d[\d,]*(?:\.\d+)?         # the number
    \s*
    (?:%|[kKmMbB](?![\w]))?    # optional percent or magnitude suffix
    """,
    re.VERBOSE,
)

# The only shape allowed through: a four digit year, unadorned. A year carrying
# a currency mark, a percent or a magnitude suffix is a figure that happens to
# look like a year and is not exempt.
_YEAR = re.compile(r"^(?:19|20)\d{2}$")


def _is_year(token: str) -> bool:
    return bool(_YEAR.match(token.strip()))


def _confidential_regions() -> list[tuple[str, int, str]]:
    """Every (label, line number, line) inside a confidential region.

    Line numbers are 1-based and refer to the file the region came from, so a
    failure can be located without the message carrying its contents.
    """
    out: list[tuple[str, int, str]] = []
    for path in _corpus_files():
        rel = path.relative_to(settings.knowledge_dir).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines()

        if rel in CONFIDENTIAL_FILES:
            out.extend((rel, i, line) for i, line in enumerate(lines, 1))
            continue

        # Walk sections. A confidential heading opens a region that runs until
        # the next heading at the same or a shallower level, which is what
        # keeps a sibling section from being swept in with it.
        depth: int | None = None
        for i, line in enumerate(lines, 1):
            heading = _HEADING.match(line)
            if heading:
                level = len(heading.group(1))
                if depth is not None and level <= depth:
                    depth = None
                if depth is None and CONFIDENTIAL_HEADING is not None \
                        and CONFIDENTIAL_HEADING.search(heading.group(2)):
                    depth = level
            if depth is not None:
                out.append((rel, i, line))
    return out

# A phone number as anyone actually writes one: 555-123-4567, (555) 123-4567,
# 555.123.4567, +1 555 123 4567. Deliberately requires separators or a country
# code, so a ten-digit run inside a URL or an id is not a match.
PHONE = re.compile(
    r"(?:\+?1[\s.\-]?)?(?:\(\d{3}\)|\b\d{3})[\s.\-]\d{3}[\s.\-]\d{4}\b"
)
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")


def _corpus_files():
    return sorted(settings.knowledge_dir.rglob("*.md"))


def test_corpus_is_not_empty():
    files = _corpus_files()
    assert files, "knowledge/ has no markdown files; the chatbot would be grounded in nothing"


def test_confidential_regions_are_covered():
    """The scanner is actually looking at something.

    A rule that silently matches no text passes forever. If a confidential file
    is renamed or the resume heading is reworded, this fails rather than letting
    the figure rule quietly become a no-op.
    """
    if not CONFIDENTIAL_FILES and CONFIDENTIAL_HEADING is None:
        pytest.skip("this corpus declares no confidential regions")

    regions = _confidential_regions()
    files = {rel for rel, _, _ in regions}
    for expected in CONFIDENTIAL_FILES:
        assert expected in files, (
            f"{expected} is named in always_on_sections but is not in the "
            "corpus. If it moved, update profile.json; the figure rule below "
            "is scoped to it."
        )
    if CONFIDENTIAL_HEADING is not None:
        assert any(rel != f for rel in files for f in CONFIDENTIAL_FILES) or files, (
            "confidential_heading_pattern is set but matched no heading in any "
            "corpus file. A reworded heading silently drops it out of scope."
        )


def test_no_bare_figures_in_confidential_regions():
    """The engagement is declared confidential, so no count may appear at all.

    A confidential project file tells the model not to state or estimate pipeline size, deal
    counts, prospect counts, valuations or revenue. That instruction is only as
    good as the corpus behind it: a number the retriever can find is a number
    the drafter can quote, and the judge only strips what it can recognise as
    confidential. Keeping the figures out of the index is the control that does
    not depend on a model behaving.

    Any cardinal is refused rather than any *known* cardinal, because the
    figures that matter are the ones nobody has thought of yet. Years are the
    single exception, since a project needs a date.

    The message names the location and not the value, on purpose: pytest output
    is a log, and a log is a publication.
    """
    offenders: list[str] = []
    for rel, lineno, line in _confidential_regions():
        found = [m.group(0) for m in _FIGURE.finditer(line)]
        if any(not _is_year(token) for token in found):
            offenders.append(f"    knowledge/{rel}:{lineno}")

    assert not offenders, (
        "a bare numeric figure appears inside a confidential region of the "
        "grounding corpus. Everything under knowledge/ is chunked, indexed and "
        "quotable, so a figure here is a published figure. Open the line and "
        "either remove the number or move the sentence out of the confidential "
        "section. Locations (values deliberately not printed, since this "
        "message goes to a log):\n" + "\n".join(offenders)
    )


def test_no_todo_markers_in_corpus():
    """A TODO marker is invisible on the page and retrievable in the index.

    The corpus is ingested as raw markdown, comment syntax and all, so
    "<!-- TODO: expand this -->" becomes a chunk competing with real answers.
    This repo shipped that way once; scripts/setup_profile.py carries the
    write-up of why it stopped scaffolding them.
    """
    hits = []
    for path in _corpus_files():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "<!-- TODO" in line or "<!--TODO" in line:
                hits.append(f"{path.name}:{i}: {line.strip()}")
    assert not hits, (
        "TODO markers in knowledge/ are ingested as retrievable context; they are "
        "invisible on the page but not to the chatbot:\n" + "\n".join(hits)
    )


def test_resume_md_carries_no_email():
    """CLAUDE.md: keep private contact details out of knowledge/resume.md.

    That file is rendered publicly into the Resume section *and* fed to
    retrieval, so an address there is both scrapeable and quotable.
    """
    path = settings.knowledge_dir / "resume.md"
    found = EMAIL.findall(path.read_text(encoding="utf-8"))
    assert not found, (
        f"knowledge/resume.md contains an email address ({found}). That file "
        "renders publicly on the page and is fed to the chatbot."
    )


def test_resume_md_carries_no_phone_number():
    path = settings.knowledge_dir / "resume.md"
    found = PHONE.findall(path.read_text(encoding="utf-8"))
    assert not found, (
        f"knowledge/resume.md contains something shaped like a phone number "
        f"({found}). That file renders publicly on the page."
    )
