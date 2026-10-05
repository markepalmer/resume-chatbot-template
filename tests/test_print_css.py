"""The print stylesheet's hide list against the template's sections.

The résumé PDF is the page printed with every other section hidden, and the
hide list in `@media print` is an explicit list of classes rather than a rule
about position, for the reason written above it in styles.css. The cost of that
choice is drift: a section added to the page is not hidden until somebody
remembers this list.

It has now drifted twice. `.career` and `.results` were added long after the
block was written and grew a career arc and a results chart into the PDF; then
`.evals` did the same, and the eval panel printed above the résumé. Both were
found by looking at a PDF, which is the expensive way.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "index.html"
STYLES = ROOT / "static" / "styles.css"

# The one section the PDF exists to carry.
PRINTED = {"resume"}


def _print_block() -> str:
    css = STYLES.read_text(encoding="utf-8")
    start = css.index("@media print")
    depth, i = 0, start
    while i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start:i + 1]
        i += 1
    raise AssertionError("the @media print block is not closed")


def _sections() -> set[str]:
    import re
    return {m.split()[0] for m in re.findall(r'<section class="([^"]+)"', TEMPLATE.read_text(encoding="utf-8"))}


def test_every_section_is_either_printed_or_hidden():
    block = _print_block()
    missing = sorted(
        cls for cls in _sections() - PRINTED
        if f".{cls}," not in block and f".{cls} " not in block
    )
    assert not missing, (
        "sections on the page that the print stylesheet does not hide: "
        f"{missing}. The résumé PDF is this page printed, so each of these "
        "prints above the résumé. Add it to the hide list in @media print, or "
        "to PRINTED here if it genuinely belongs in the PDF."
    )


def test_the_resume_itself_is_not_hidden():
    """The inverse mistake: hiding everything and shipping an empty PDF."""
    block = _print_block()
    for pattern in (".resume,", ".resume "):
        assert f"{pattern}\n  display: none" not in block
    assert ".resume-body" not in block.split("display: none")[0], (
        "the résumé body is in the hide list, which would print an empty PDF"
    )


@pytest.mark.parametrize("cls", sorted(_sections() - PRINTED))
def test_hide_list_names_each_section(cls):
    """Named per section so a failure says which one is missing."""
    assert f".{cls}," in _print_block() or f".{cls} " in _print_block()
