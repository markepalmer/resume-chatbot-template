"""The saved starter answers, and the guard that stops them going stale.

All offline and free. The expensive half of this feature (calling Groq) is
`tools/build_starter_answers.py`; what is worth testing is the cheap half, which
is the rule that saved output is dropped the moment the corpus moves under it.
"""

import json

import pytest

from app import profile as profile_mod
from app.config import settings
from app.fingerprint import corpus_fingerprint
from app.profile import load_profile, load_starter_answers, starter_answers_json


@pytest.fixture(autouse=True)
def _clear_caches():
    """Both loaders are lru_cached, so each test needs a clean read."""
    load_starter_answers.cache_clear()
    starter_answers_json.cache_clear()
    yield
    load_starter_answers.cache_clear()
    starter_answers_json.cache_clear()


def _write(tmp_path, monkeypatch, payload):
    path = tmp_path / "starter_answers.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(profile_mod, "STARTER_ANSWERS_PATH", path)
    return path


def _entry(question="What was hard about the job scheduler?"):
    return {"question": question, "answer": "Exactly once delivery under retries.", "sources": ["resume.md"]}


def test_missing_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_mod, "STARTER_ANSWERS_PATH", tmp_path / "absent.json")
    assert load_starter_answers() == []


def test_matching_fingerprint_is_served(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"fingerprint": corpus_fingerprint(), "answers": [_entry()]})
    assert len(load_starter_answers()) == 1


def test_stale_fingerprint_drops_every_answer(tmp_path, monkeypatch):
    """The whole point of the feature's safety story.

    A saved answer outliving its corpus is the failure that matters: the page
    would keep asserting something the documents no longer say, with citations,
    and nothing would surface it. Dropping the file costs tokens. Serving it
    costs the truth of the page.
    """
    _write(tmp_path, monkeypatch, {"fingerprint": "0000000000000000", "answers": [_entry()]})
    assert load_starter_answers() == []


def test_corpus_edit_changes_the_fingerprint(tmp_path, monkeypatch):
    """A fingerprint that did not move on a corpus edit would guard nothing."""
    before = corpus_fingerprint()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "resume.md").write_text("# Resume\n\nSomething else entirely.\n", encoding="utf-8")
    monkeypatch.setattr(settings, "knowledge_dir", knowledge)
    assert corpus_fingerprint() != before


def test_malformed_file_falls_back_to_live(tmp_path, monkeypatch):
    path = tmp_path / "starter_answers.json"
    path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(profile_mod, "STARTER_ANSWERS_PATH", path)
    assert load_starter_answers() == []


def test_entries_without_an_answer_are_dropped(tmp_path, monkeypatch):
    _write(
        tmp_path,
        monkeypatch,
        {
            "fingerprint": corpus_fingerprint(),
            "answers": [_entry(), {"question": "Empty?", "answer": ""}, {"answer": "no question"}],
        },
    )
    assert len(load_starter_answers()) == 1


def test_embedded_json_escapes_a_closing_script_tag(tmp_path, monkeypatch):
    """An answer containing `</script>` must not be able to close its own block."""
    hostile = {"question": "Q?", "answer": "text </script><img src=x> more", "sources": []}
    _write(tmp_path, monkeypatch, {"fingerprint": corpus_fingerprint(), "answers": [hostile]})
    embedded = starter_answers_json()
    assert "</script>" not in embedded
    assert json.loads(embedded.replace("<\\/", "</"))[0]["answer"] == hostile["answer"]


def test_saved_questions_are_ones_the_page_actually_offers():
    """A saved answer to a question no chip asks is dead weight in every page.

    Skipped when nothing is saved yet, which is the state a fresh fork is in.
    """
    saved = load_starter_answers()
    if not saved:
        pytest.skip("no starter answers built yet")
    offered = {q.strip().lower() for q in load_profile()["starter_questions"]}
    for entry in saved:
        assert entry["question"].strip().lower() in offered, (
            f"{entry['question']!r} is saved but is not one of profile.json's "
            "starter_questions, so it ships to every visitor and answers nothing."
        )
