"""The confidentiality instruction reaches the model on every call.

Why this is a test and not a comment: the guarantee it protects fails silently
in both directions. If the section stops being injected, nothing errors, no
score moves, and the only symptom is the model answering a withheld question
from whatever the public passages happen to imply. If the heading is renamed,
the entry in always_on_sections quietly matches nothing.

The reason the mechanism exists is measured and written up in docs/evals.md
under "Why confidentiality sections are always-on": a Confidentiality section
ranked SEVENTH against a top_k of 5, so the rule never reached the prompt and
the model answered from the public passages instead of declining.

Fast: reads knowledge/. No index, no embeddings, no Groq call.
"""

import pytest

from app.retriever import _always_on_sections, _extract_section, build_always_on_summary

_ALWAYS_ON_SECTIONS = _always_on_sections()


def test_confidentiality_marking_is_always_injected():
    summary = build_always_on_summary()
    if not _ALWAYS_ON_SECTIONS:
        pytest.skip("this profile declares no always_on_sections")

    # Asserted by source marker rather than by a phrase from any one corpus:
    # a literal like "scoring criteria" pins the test to one owner's documents
    # and fails for every fork, while proving less. What matters is that each
    # configured section actually arrived.
    for source, _heading in _ALWAYS_ON_SECTIONS:
        assert f"[source: {source}]" in summary, (
            f"{source} is listed in always_on_sections but its section is not "
            "in the always-on block, so whether the marking reaches the model "
            "is back to depending on how the visitor phrased the question"
        )
    assert "confidential" in summary.lower()


def test_the_corpus_marks_and_the_prompts_instruct():
    """The division of labour that keeps runtime instructions out of the corpus.

    knowledge/ is FACTS, which a visitor may see quoted back at them. The
    behaviour on a marking ("say it is confidential and offer the design
    instead") is owner-independent, so it belongs in the prompt, where it is
    never retrievable and never quotable.

    Written as a test because the corpus half is edited by hand by whoever forks
    this, and an imperative creeping back in is invisible: it would still work,
    while putting a list of exactly what to probe for into every prompt.
    """
    from app.prompts import DRAFTER_SYSTEM, JUDGE_SYSTEM

    summary = build_always_on_summary()
    for source, heading in _ALWAYS_ON_SECTIONS:
        from app.config import settings
        from app.ingest import load_documents

        docs = {d.metadata["source"]: d.page_content for d in load_documents(settings.knowledge_dir)}
        if source not in docs:
            continue
        body = (_extract_section(docs[source], heading) or "").lower()
        for imperative in ("do not state", "do not disclose", "if asked", "you must", "never say"):
            assert imperative not in body, (
                f"'{imperative}' is an instruction to the model, and it is sitting in "
                f"{source} where it is retrievable, quotable, and dumpable. State what "
                "is confidential; let the prompt decide what to do about it."
            )

    assert "confidential" in DRAFTER_SYSTEM and "confidential" in JUDGE_SYSTEM, (
        "the corpus now only marks, so if the prompts stop acting on a marking "
        "nothing does"
    )
    assert summary.strip()


def test_every_declared_section_actually_resolves():
    """A source that exists but has lost its heading must fail loudly."""
    from app.config import settings
    from app.ingest import load_documents

    docs = {d.metadata["source"]: d.page_content for d in load_documents(settings.knowledge_dir)}
    for source, heading in _ALWAYS_ON_SECTIONS:
        if source not in docs:
            continue
        assert _extract_section(docs[source], heading) is not None, (
            f"_ALWAYS_ON_SECTIONS names '{heading}' in {source}, which no longer "
            "has that heading"
        )


def test_missing_heading_raises_rather_than_silently_dropping(monkeypatch):
    from langchain_core.documents import Document

    from app import retriever

    source, heading = _ALWAYS_ON_SECTIONS[0]
    gutted = Document(
        page_content="# Project\n\n## Summary\n\nNothing confidential here.",
        metadata={"source": source},
    )
    monkeypatch.setattr(retriever, "_ALWAYS_ON_SOURCES", ())
    monkeypatch.setattr("app.ingest.load_documents", lambda _dir: [gutted])

    with pytest.raises(ValueError, match=heading):
        retriever.build_always_on_summary()


def test_extract_section_stops_at_the_next_sibling_heading():
    text = (
        "# Doc\n\n"
        "## Confidentiality\n\nDo not disclose figures.\n\n"
        "### Detail\n\nStill part of the section.\n\n"
        "## Summary\n\nPublic prose.\n"
    )
    body = _extract_section(text, "Confidentiality")
    assert "Do not disclose figures." in body
    assert "Still part of the section." in body, "a subsection was dropped"
    assert "Public prose." not in body, "capture ran past the next '##'"


def test_extract_section_returns_none_when_absent():
    assert _extract_section("# Doc\n\n## Summary\n\nText.\n", "Confidentiality") is None
