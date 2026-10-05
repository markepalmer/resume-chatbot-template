"""The prompt templates format cleanly with the kwargs the pipeline passes.

Every one of these is a plain `str.format` call at runtime inside
`ChatEngine`, so a renamed placeholder does not fail at import; it raises
KeyError on the first question a visitor asks, which is the first time anyone
finds out. A stray unpaired brace is worse: `str.format` raises ValueError on
some shapes and silently emits the literal on others.
"""

import re
import string

import pytest

from app.profile import load_profile
from app.prompts import DRAFTER_SYSTEM, JUDGE_SYSTEM, JUDGE_USER

CONTEXT = "Source: test. The owner built a thing."
QUESTION = "What did they build?"
DRAFT = "They built a thing."

# The kwargs each template is rendered with in app/chat.py. Keeping the
# expectation here means renaming a placeholder fails a test rather than a
# visitor's first question.
EXPECTED = {
    "DRAFTER_SYSTEM": {"name", "context"},
    "JUDGE_SYSTEM": {"name", "context"},
    "JUDGE_USER": {"question", "draft"},
}

TEMPLATES = {
    "DRAFTER_SYSTEM": DRAFTER_SYSTEM,
    "JUDGE_SYSTEM": JUDGE_SYSTEM,
    "JUDGE_USER": JUDGE_USER,
}


def _fields(template: str) -> set[str]:
    return {
        name.split(".")[0].split("[")[0]
        for _, name, _, _ in string.Formatter().parse(template)
        if name
    }


@pytest.mark.parametrize("label", sorted(TEMPLATES))
def test_template_placeholders_are_exactly_what_is_passed(label):
    found = _fields(TEMPLATES[label])
    assert found == EXPECTED[label], (
        f"{label} takes {sorted(found)} but app/chat.py renders it with "
        f"{sorted(EXPECTED[label])}. A mismatch raises KeyError on the first "
        "question asked, not at import."
    )


@pytest.mark.parametrize("label", sorted(TEMPLATES))
def test_template_formats_and_leaves_no_stray_braces(label):
    values = {
        "name": load_profile()["known_as"],
        "context": CONTEXT,
        "question": QUESTION,
        "draft": DRAFT,
    }
    template = TEMPLATES[label]
    rendered = template.format(**{k: values[k] for k in EXPECTED[label]})

    leftover = re.findall(r"\{[^{}]*\}", rendered)
    assert not leftover, (
        f"{label} left unfilled placeholders after formatting: {leftover}. "
        "These reach the model verbatim as literal text."
    )
    assert "{" not in rendered and "}" not in rendered, (
        f"{label} contains an unpaired brace, which reaches the model as-is"
    )


def test_prompts_carry_the_owner_name_and_are_not_hardcoded():
    """CLAUDE.md: no personal detail is hardcoded in app/.

    The name is injected from profile.json, so it must appear in the rendered
    prompt and must not appear in the template source.
    """
    name = load_profile()["known_as"]
    for label in ("DRAFTER_SYSTEM", "JUDGE_SYSTEM"):
        template = TEMPLATES[label]
        rendered = template.format(name=name, context=CONTEXT)
        assert name in rendered, (
            f"{label} does not mention the owner's name after formatting; the "
            "grounding instruction is written around it"
        )
        assert name.lower() not in template.lower(), (
            f"{label} hardcodes {name!r}. CLAUDE.md: personal details belong in "
            "profile.json, not in app/."
        )


def test_grounding_rules_survive_edits():
    """The three instructions the pipeline exists to enforce.

    Loose keyword checks on purpose: this is not a diff of the prompt text, it
    is a floor under it. Losing any of these turns a grounded assistant into an
    ordinary one without changing a line of pipeline code.
    """
    drafter = DRAFTER_SYSTEM.lower()
    judge = JUDGE_SYSTEM.lower()
    assert "only the context" in drafter or "only from the provided context" in drafter, (
        "DRAFTER_SYSTEM no longer restricts the model to the provided context"
    )
    assert "confidential" in drafter, (
        "DRAFTER_SYSTEM no longer tells the model to honour a confidentiality "
        "marker in the corpus. Any knowledge/ file with a Confidentiality "
        "section relies on it."
    )
    assert "confidential" in judge, (
        "JUDGE_SYSTEM no longer strips disclosed confidential figures, which is "
        "the backstop for the drafter's rule"
    )
    assert "third person" in drafter and "third person" in judge
