"""Load the knowledge base, chunk it, embed it, and build an in-memory FAISS index.

The index is never committed. In the deployed image it is embedded once at build
time and loaded from ``settings.index_dir`` (see ``save_index``); everywhere else
it is built in process on first use. Every chunk carries a ``source`` in its
metadata (the relative path of the doc it came from) so the answer pipeline can
cite where a fact came from.
"""

import hashlib
import json
import logging
from pathlib import Path

from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from .config import settings

# Split on the heading levels the corpus actually uses. Order matters: the
# splitter treats these as a hierarchy, so a chunk under "## Projects / ### Job scheduler"
# carries both.
_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]

# A chunk shorter than this that is nothing but a heading is dropped. It is not
# a length heuristic in general: real short chunks are kept. See _chunk below.
_MIN_HEADING_CHUNK = 80


def load_documents(knowledge_dir: Path) -> list[Document]:
    """Read every .md file under knowledge_dir into a Document with a source label."""
    docs: list[Document] = []
    for path in sorted(knowledge_dir.rglob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        source = path.relative_to(knowledge_dir).as_posix()
        docs.append(Document(page_content=text, metadata={"source": source}))
    return docs


def chunk_documents(documents: list[Document]) -> list[Document]:
    """Split by markdown heading first, then by size within each section.

    The previous version passed headings to RecursiveCharacterTextSplitter as
    separators (``["\\n## ", "\\n### ", ...]``). That splits *at* the heading,
    which strands the heading as its own chunk whenever the block after it does
    not fit in the remaining budget. Measured on this corpus it produced eight
    such orphans, including a nine-character '## Skills'.

    An orphan is worse than wasted space. A bare heading embeds as a near
    perfect match for the topic it names, so it wins a top-k slot on exactly the
    questions it is least able to answer: "what are his skills?" retrieved
    '## Skills', '## What he brings to these roles' and a file title, three of
    five slots carrying no information, with the actual skills list absent.

    Splitting on headings *structurally* and then by size within each section
    fixes it at the source. Every chunk is then prefixed with its heading trail,
    so a chunk from the middle of a section still says what it is about. The
    prefix carries the trail only, not the source: ``retriever.format_context``
    already stamps ``[source: ...]`` on every chunk, and repeating it would pay
    for the same string twice on every request.
    """
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_HEADERS, strip_headers=True
    )
    body_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        # No heading separators here: sectioning already happened above, and
        # leaving them in would reintroduce the orphan.
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    chunks: list[Document] = []
    for doc in documents:
        source = doc.metadata["source"]
        for section in header_splitter.split_text(doc.page_content):
            trail = " > ".join(
                section.metadata[key] for _, key in _HEADERS if section.metadata.get(key)
            )
            for piece in body_splitter.split_text(section.page_content):
                text = piece.strip()
                # A section that is a heading and nothing else (the document has
                # a '## Skills' with its content under a deeper level, say)
                # produces an empty body. Nothing to index.
                if not text:
                    continue
                if len(text) < _MIN_HEADING_CHUNK and text.lstrip().startswith("#"):
                    continue
                body = f"[{trail}]\n{text}" if trail else text
                chunks.append(Document(page_content=body, metadata={"source": source}))
    return chunks


_INDEX_FILE = "index.json"


def index_key() -> str:
    """Hash every input that decides the index's contents.

    The corpus, the chunking knobs, the embedding model, and this module's own
    source, since the chunker lives here and a change to it changes every chunk.
    Narrower than ``fingerprint.corpus_fingerprint`` on purpose: a prompt or a
    model name changes the answer but not the index, and should not throw a good
    index away.
    """
    parts = {
        "docs": {
            path.relative_to(settings.knowledge_dir).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(settings.knowledge_dir.rglob("*.md"))
        },
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "embedding_model": settings.embedding_model,
        "ingest": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _chunks() -> list[Document]:
    documents = load_documents(settings.knowledge_dir)
    if not documents:
        raise RuntimeError(
            f"No knowledge documents found under {settings.knowledge_dir}. "
            "Add markdown files to the knowledge/ directory."
        )
    return chunk_documents(documents)


def _embeddings() -> FastEmbedEmbeddings:
    return FastEmbedEmbeddings(model_name=settings.embedding_model)


def save_index(directory: Path) -> Path:
    """Embed the corpus and write it to ``directory`` for ``build_vectorstore`` to load.

    Run at image build time. Embedding is the whole cost of building the index:
    measured at 9 to 23 s for the 70 chunks on an 8-core laptop, and the cause of
    a ~45 s first answer on Cloud Run's single vCPU after every cold start.

    Vectors and text as JSON rather than ``FAISS.save_local``, which pickles the
    docstore and can only be read back with ``allow_dangerous_deserialization``.
    A flat index over 70 vectors is rebuilt from raw vectors in milliseconds, so
    the pickle would buy nothing. The floats round-trip exactly: float32 widens
    to a double losslessly, and FAISS narrows it back to the same float32.
    """
    chunks = _chunks()
    vectors = _embeddings().embed_documents([c.page_content for c in chunks])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _INDEX_FILE
    path.write_text(
        json.dumps(
            {
                "key": index_key(),
                "chunks": [
                    {"text": c.page_content, "source": c.metadata["source"], "vector": v}
                    for c, v in zip(chunks, vectors)
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _load_index(directory: Path, embeddings: FastEmbedEmbeddings) -> FAISS | None:
    """The saved index, or None when it is missing, unreadable, or stale.

    Stale is the one that matters. An index built from yesterday's corpus loads
    fine and answers confidently from text that no longer exists, so a key that
    does not match is refused outright and the caller builds a fresh one.
    """
    path = directory / _INDEX_FILE
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("No usable prebuilt index at %s (%s); building one.", path, exc)
        return None
    if saved.get("key") != index_key():
        logging.warning("Prebuilt index at %s is stale for this corpus; building one.", path)
        return None
    chunks = saved["chunks"]
    return FAISS.from_embeddings(
        [(c["text"], c["vector"]) for c in chunks],
        embeddings,
        metadatas=[{"source": c["source"]} for c in chunks],
    )


def build_vectorstore() -> FAISS:
    """The FAISS index over the knowledge base: loaded if prebuilt, built otherwise.

    The embedding model is loaded either way, since every question is embedded
    against the index. That costs about a second; the corpus embedding it skips
    is what costs the rest.
    """
    embeddings = _embeddings()
    if settings.index_dir is not None:
        loaded = _load_index(settings.index_dir, embeddings)
        if loaded is not None:
            return loaded
    return FAISS.from_documents(_chunks(), embeddings)
