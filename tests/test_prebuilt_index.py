"""The prebuilt index: loaded when it matches the corpus, never when it does not.

The image embeds the corpus at build time so the first question after a cold
start does not (see ``save_index``). The danger of a saved index is the same as
the danger of saved answers: it outlives the corpus it was built from and keeps
answering from text that no longer exists. So the property pinned here is not
"it loads", it is "it loads only when it is still true".

No embedding model and no network. A fake embedder stands in for fastembed, and
``FAISS.from_documents`` is the in-process build path, so whether it was called
is the observable for "the saved index was refused".
"""

import pytest
from langchain_core.embeddings import Embeddings

from app import ingest
from app.config import settings


class FakeEmbeddings(Embeddings):
    """Deterministic 4-dim vectors from the text's length. Enough to index."""

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text):
        n = float(len(text))
        return [n, n % 7, n % 3, 1.0]


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "about.md").write_text("# About\n\nWrites Python and SQL.\n", encoding="utf-8")
    (knowledge / "roles.md").write_text("# Roles\n\nOpen to data roles.\n", encoding="utf-8")
    monkeypatch.setattr(settings, "knowledge_dir", knowledge)
    monkeypatch.setattr(ingest, "_embeddings", FakeEmbeddings)
    return knowledge


@pytest.fixture
def builds(monkeypatch):
    """Record every in-process build, and let it run."""
    calls = []
    real = ingest.FAISS.from_documents

    def spy(docs, embedding, **kw):
        calls.append(len(docs))
        return real(docs, embedding, **kw)

    monkeypatch.setattr(ingest.FAISS, "from_documents", spy)
    return calls


def test_a_matching_index_is_loaded_not_rebuilt(corpus, tmp_path, monkeypatch, builds):
    ingest.save_index(tmp_path / "index")
    monkeypatch.setattr(settings, "index_dir", tmp_path / "index")

    store = ingest.build_vectorstore()

    assert builds == [], "a fresh prebuilt index was ignored and the corpus embedded again"
    assert store.index.ntotal == len(ingest.chunk_documents(ingest.load_documents(corpus)))
    sources = {d.metadata["source"] for d in store.docstore._dict.values()}
    assert sources == {"about.md", "roles.md"}


def test_a_corpus_edit_makes_the_saved_index_stale(corpus, tmp_path, monkeypatch, builds):
    ingest.save_index(tmp_path / "index")
    monkeypatch.setattr(settings, "index_dir", tmp_path / "index")
    (corpus / "roles.md").write_text("# Roles\n\nOpen to ML roles.\n", encoding="utf-8")

    ingest.build_vectorstore()

    assert builds, "an index built from the old corpus was loaded after knowledge/ changed"


def test_a_chunking_change_makes_the_saved_index_stale(corpus, tmp_path, monkeypatch, builds):
    ingest.save_index(tmp_path / "index")
    monkeypatch.setattr(settings, "index_dir", tmp_path / "index")
    monkeypatch.setattr(settings, "chunk_size", settings.chunk_size + 1)

    ingest.build_vectorstore()

    assert builds, "an index chunked under different settings was loaded"


@pytest.mark.parametrize("contents", [None, "", "{not json"])
def test_a_missing_or_broken_index_falls_back_to_building(
    corpus, tmp_path, monkeypatch, builds, contents
):
    directory = tmp_path / "index"
    if contents is not None:
        directory.mkdir()
        (directory / "index.json").write_text(contents, encoding="utf-8")
    monkeypatch.setattr(settings, "index_dir", directory)

    ingest.build_vectorstore()

    assert builds, "an unusable index should fall back to a build, not fail the chat"


def test_no_index_dir_builds_in_process(corpus, monkeypatch, builds):
    monkeypatch.setattr(settings, "index_dir", None)

    ingest.build_vectorstore()

    assert builds
