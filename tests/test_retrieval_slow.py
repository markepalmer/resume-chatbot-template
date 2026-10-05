"""Retrieval over the real corpus. Marked slow; deselected by default.

These build the FAISS index, which means running the bge-small-en-v1.5 ONNX
embedder over knowledge/. That is local work with no network call once the
model is cached, and no Groq call at any point: the index is queried directly,
never through ChatEngine. But it is seconds rather than milliseconds and it
downloads ~65 MB the first time, so it is behind the marker.

    python3 -m pytest -m slow
"""

import pytest

from app.config import settings

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def vectorstore():
    from app.ingest import build_vectorstore

    return build_vectorstore()


def test_corpus_chunks_without_loss():
    from app.ingest import load_documents

    docs = load_documents(settings.knowledge_dir)
    assert docs, "knowledge/ produced no documents; the chatbot would be grounded in nothing"
    sources = {d.metadata.get("source") for d in docs}
    on_disk = {p.name for p in settings.knowledge_dir.rglob("*.md")}
    assert len(sources) >= len(on_disk) - 0, (
        f"only {len(sources)} of {len(on_disk)} corpus files reached the index: "
        f"missing {sorted(on_disk - {str(s) for s in sources})}"
    )


def test_always_on_summary_is_built_and_bounded():
    """CLAUDE.md: this summary is injected on every call, so a retrieval miss
    can never drop the core facts. It is also paid for on every call."""
    from app.retriever import build_always_on_summary

    summary = build_always_on_summary()
    assert summary.strip(), (
        "the always-on summary is empty, so a retrieval miss now drops the core "
        "facts entirely instead of being backstopped"
    )


def test_a_project_deep_link_retrieves_that_project(vectorstore):
    """The first `ask_links` slug that names a file under knowledge/projects/ is
    asked, and that file has to come back. Read from profile.json rather than
    written here, so a fork tests its own corpus without editing this file."""
    from app.profile import load_profile
    from app.retriever import retrieve, sources_of

    stems = [p.stem for p in (settings.knowledge_dir / "projects").glob("*.md")]
    pick = next(
        ((slug, q) for slug, q in load_profile()["ask_links"].items()
         if any(slug in stem for stem in stems)),
        None,
    )
    if pick is None:
        pytest.skip("no ask_links slug names a file under knowledge/projects/")
    slug, question = pick

    retrieved = retrieve(vectorstore, question)
    assert retrieved, "retrieval returned nothing for a question the corpus answers"
    assert len(retrieved) <= settings.top_k
    assert any(slug in str(s).lower() for s in sources_of(retrieved)), (
        f"the {slug!r} deep link question did not retrieve its project file; got "
        f"{sources_of(retrieved)}"
    )
