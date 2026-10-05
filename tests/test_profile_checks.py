"""Reuse the onboarding script's own checkers against the real repo.

`scripts/setup_profile.py` already knows how to spot every one of these, but it
only ever runs when someone re-runs the interactive setup, which nobody does
after the first fork. Each checker reports by printing and returns None, so the
assertion here is on captured stdout being empty.

Reusing them rather than reimplementing is the point: a rule that lives in two
places drifts, and the version a forker sees would be the one that fell behind.
"""

import hashlib
import json
import re

import pytest

from app.profile import PROFILE_PATH
from scripts.setup_profile import (
    check_career_order,
    check_resume_dates,
    report_duplicate_skills,
    report_orphan_skills,
)


@pytest.fixture
def raw() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


# check_resume_dates prints two independent reports and the difference matters.
#
# The first is a violation: an engagement in both files carrying two different
# date ranges, or an engagement in profile.json that resume.md does not
# describe at all. The second is not, and the function's own docstring says so:
# the two files are not required to hold the same *set* of entries, because an
# arc segment is a visualisation decision and a resume line is a career fact.
# Asserting on the whole of stdout conflates them and makes a deliberate choice
# look like a defect.
_EXTRA_HEADINGS_MARKER = "resume.md heading(s) have no engagement in profile.json"

# resume.md headings that deliberately have no Career Arc segment, so the *known*
# omissions pass and a new, unexplained one still fails. Pinned by a digest of
# the heading, not its text: a heading is the owner's biography, and this file
# ships in the public template. The failure message prints both, so recording a
# new deliberate omission is a paste.
INTENTIONALLY_NOT_IN_ARC = {
    "e81c9ff1936ab855",
}


def _digest(heading: str) -> str:
    return hashlib.sha256(heading.encode("utf-8")).hexdigest()[:16]


def _split_report(out: str) -> tuple[str, str]:
    """(date problems, extra-heading notice)."""
    # The notice is introduced by a count, so the split point is the start of
    # that whole line and not the marker text itself.
    match = re.search(r"^.*" + re.escape(_EXTRA_HEADINGS_MARKER), out, re.M)
    if match:
        return out[: match.start()], out[match.end() :]
    return out, ""


def test_career_dates_agree_with_resume_md(raw, capsys):
    """CLAUDE.md: dates live in profile.json's `career` and in knowledge/resume.md.

    Both are rendered on the same page, so a disagreement shows two different
    date ranges for one job in two places a visitor can see at once, and the
    chatbot is grounded in the resume.md copy.
    """
    check_resume_dates(raw.get("career", []))
    problems, _ = _split_report(capsys.readouterr().out)
    assert not problems.strip(), (
        "profile.json's career block and knowledge/resume.md disagree. Change one "
        "and change the other, then rerun tools/build_career_arc.py:\n" + problems
    )


def test_resume_headings_missing_from_the_arc_are_the_expected_ones(raw, capsys):
    """A resume.md engagement with no arc segment is allowed, but not by accident.

    Such a heading is on the page and in the chatbot while being absent from
    the Career Arc. That is a legitimate editorial choice, so this pins the set
    rather than forbidding it: the known omission passes, a new one fails and
    has to be either added to the arc or added here on purpose.
    """
    check_resume_dates(raw.get("career", []))
    _, extras = _split_report(capsys.readouterr().out)
    found = {
        line.strip()
        for line in extras.splitlines()
        if line.startswith("    ") and line.strip() and not line.strip().startswith("so they")
    }
    unexpected = sorted(f"{h}  ({_digest(h)})" for h in found
                        if _digest(h) not in INTENTIONALLY_NOT_IN_ARC)
    assert not unexpected, (
        "resume.md heading(s) are on the page and in the chatbot but have no "
        "engagement in profile.json, so they are missing from the Career Arc:\n  "
        + "\n  ".join(unexpected)
        + "\nAdd a career entry, or add the digest in brackets to "
        "INTENTIONALLY_NOT_IN_ARC to record that the omission is deliberate."
    )


def test_no_orphan_skills(raw, capsys):
    """A career skill that is in no skill group draws no chord in the arc.

    It stays in the data and silently disappears from the figure.
    """
    report_orphan_skills(raw.get("career", []), raw.get("skills", {}))
    out = capsys.readouterr().out
    assert not out.strip(), (
        "career entries reference skills that are in no skill group, so they draw "
        "no line in the Career Arc:\n" + out
    )


def test_no_duplicate_skills(raw, capsys):
    """A skill in two groups is drawn in whichever group came last.

    `place_skills` in the arc builder keys by skill name, so the duplicate is
    overwritten. Nothing errors; one node just wears the wrong discipline's
    colour, in the network, the legend and the arc alike.
    """
    report_duplicate_skills(raw.get("skills", {}))
    out = capsys.readouterr().out
    assert not out.strip(), (
        "a skill appears in more than one group; the group is what picks its "
        "colour, so it will be drawn in the wrong one:\n" + out
    )


def test_career_entries_in_date_order(raw, capsys):
    """Shade reads as recency and is derived from list position, not from dates."""
    check_career_order(raw.get("career", []))
    out = capsys.readouterr().out
    assert not out.strip(), (
        "career entries of the same kind are not in date order. The figure shades "
        "by list position, so the shading will not match the dates:\n" + out
    )
