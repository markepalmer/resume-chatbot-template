"""The eval question set is well formed, and its labels point at real things.

A broken eval fails quietly, which is worse than no eval: an expected_sources
entry naming a document that has been renamed scores as a permanent miss, and
the number it drags down looks like a retrieval regression forever. A
must_contain_any needle that no longer appears anywhere in the corpus fails the
same way. Both are cheap to check and impossible to notice by reading a score.

Fast: parses YAML and reads knowledge/. No index, no embeddings, no network.
"""

from pathlib import Path

import pytest
import yaml

QUESTIONS = Path(__file__).resolve().parent.parent / "evals" / "questions.yaml"
KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"

VALID_BUCKETS = {"factual", "multihop", "confidential", "out_of_scope", "adversarial"}
UNSCORED = {"out_of_scope", "adversarial"}


@pytest.fixture(scope="module")
def questions() -> list[dict]:
    return yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))["questions"]


@pytest.fixture(scope="module")
def corpus_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in KNOWLEDGE.rglob("*.md")).lower()


def test_ids_are_unique(questions):
    ids = [q["id"] for q in questions]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"duplicate question ids {duplicates}; ids appear in saved reports"


def test_buckets_are_known(questions):
    for q in questions:
        assert q.get("bucket") in VALID_BUCKETS, (
            f"{q['id']} has bucket {q.get('bucket')!r}, which the harness does not score"
        )


def test_every_question_has_text(questions):
    for q in questions:
        assert q.get("question", "").strip(), f"{q['id']} has no question text"


def test_expected_sources_exist_on_disk(questions):
    """A label naming a moved document scores as a permanent, invisible miss."""
    for q in questions:
        for source in q.get("expected_sources") or []:
            assert (KNOWLEDGE / source).is_file(), (
                f"{q['id']} expects {source!r}, which is not a file under knowledge/. "
                "Either the document moved or the label is a typo; both make this "
                "question unscoreable forever."
            )


def test_scored_questions_have_labels(questions):
    for q in questions:
        if q.get("bucket") in UNSCORED:
            continue
        assert q.get("expected_sources"), (
            f"{q['id']} is in a scored bucket but labels no expected source, so it "
            "can only ever score zero"
        )


def test_unscored_questions_have_no_labels(questions):
    """out_of_scope and adversarial are refusal tests; a label there is a mistake."""
    for q in questions:
        if q.get("bucket") not in UNSCORED:
            continue
        assert not q.get("expected_sources"), (
            f"{q['id']} is {q['bucket']} but names expected sources. Retrieval always "
            "returns its top k, so there is no correct source for these; they are "
            "scored on refusal by the groundedness harness instead."
        )


def test_must_contain_needles_exist_in_the_corpus(questions, corpus_text):
    """A needle absent from knowledge/ can never be retrieved, so it tests nothing.

    This is the check that catches a scrubbed figure: if a needle disappears
    because the corpus was edited, the question silently becomes unpassable
    rather than failing loudly here.
    """
    for q in questions:
        needles = q.get("must_contain_any") or []
        if not needles:
            continue
        missing = [n for n in needles if n.lower() not in corpus_text]
        assert len(missing) < len(needles), (
            f"{q['id']}: none of its must_contain_any needles {needles} appear "
            f"anywhere in knowledge/, so the question can never pass. Either the "
            f"corpus changed or the needles are wrong."
        )
