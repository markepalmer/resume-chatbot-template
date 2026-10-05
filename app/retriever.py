"""Retrieval seam plus the always-on profile summary (the hybrid grounding strategy).

Every answer is grounded on two things:

1. The top-k chunks retrieved from FAISS for the specific question.
2. A compact, always-on profile summary that is injected on every call so that a
   retrieval miss can never drop the core facts about the site's owner.
"""

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from .config import settings

# Docs whose full text always seeds the always-on summary. These are small and
# high value, so they are worth paying for on every request.
_ALWAYS_ON_SOURCES = ("resume.md", "roles-open-to.md")

# Individual SECTIONS injected on every call even though the rest of their
# document is not. Each entry is (source, heading), and the list itself now
# lives in profile.json under `always_on_sections`, because the filenames in it
# are the site owner's corpus rather than the application's vocabulary. The
# reasoning below is why the mechanism exists at all and still applies.
#
# This exists because a confidentiality instruction is policy, not content, and
# policy must not compete on cosine similarity against content. A marking that
# has to win a similarity contest before it applies is not a guarantee: whether
# the rule arrives is a function of how the QUESTION was phrased, and the
# visitor writes the question. The obliquely-worded asks that most need the rule
# are exactly the ones least likely to retrieve it.
#
# Note what this does NOT do: it adds no new information to the corpus. It ships
# the sentence saying a thing is withheld, not the thing being withheld.
#
# The measurements behind this, including the ranking that put a Confidentiality
# section SEVENTH against a top_k of 5 and what the model said in its absence,
# are in docs/evals.md under "Why confidentiality sections are always-on".


def _always_on_sections() -> list[tuple[str, str]]:
    """Read the (source, heading) pairs from profile.json.

    Imported here rather than at module scope because app/profile.py is on the
    page's import path and this module is not: the page route must never pull
    FAISS and the embedding stack in behind it (CLAUDE.md).
    """
    from .profile import load_profile

    return [(str(s), str(h)) for s, h in load_profile()["always_on_sections"]]


def _extract_section(text: str, heading: str) -> str | None:
    """Return the body of the markdown section named ``heading``, or None.

    Captures from the heading line to the next heading of the same or a higher
    level, so a '## Confidentiality' holding '### Details' keeps the subsection.
    """
    lines = text.splitlines()
    start: int | None = None
    level = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        hashes = len(stripped) - len(stripped.lstrip("#"))
        title = stripped[hashes:].strip()
        if start is None:
            if title.casefold() == heading.casefold():
                start, level = i, hashes
            continue
        if hashes <= level:
            return "\n".join(lines[start:i]).strip()
    if start is None:
        return None
    return "\n".join(lines[start:]).strip()


def build_always_on_summary() -> str:
    """Concatenate the highest-value docs and sections into the always-on context."""
    from .ingest import load_documents

    docs = {d.metadata["source"]: d.page_content for d in load_documents(settings.knowledge_dir)}
    parts: list[str] = []
    for source in _ALWAYS_ON_SOURCES:
        if source in docs:
            parts.append(docs[source])
    for source, heading in _always_on_sections():
        # A fork that deleted the document entirely is a fork that has no such
        # policy to enforce, so skip it. A document that is still here but has
        # lost the named heading is the dangerous case: the guarantee would
        # vanish with nothing to show for it, which is the silent-absence
        # failure this repo keeps paying for elsewhere. Fail loudly instead.
        if source not in docs:
            continue
        body = _extract_section(docs[source], heading)
        if body is None:
            raise ValueError(
                f"{source} has no '{heading}' section, but it is listed in "
                "always_on_sections in profile.json. Either restore the "
                "heading or drop the entry; leaving it means the instruction "
                "silently stops reaching the model."
            )
        parts.append(f"[source: {source}]\n{body}")
    return "\n\n".join(parts)


def format_context(retrieved: list[Document], always_on: str) -> str:
    """Render retrieved chunks plus the always-on summary into a single context block."""
    blocks = [f"[always-on profile]\n{always_on}"]
    for doc in retrieved:
        source = doc.metadata.get("source", "unknown")
        blocks.append(f"[source: {source}]\n{doc.page_content}")
    return "\n\n---\n\n".join(blocks)


def retrieve(vectorstore: FAISS, question: str) -> list[Document]:
    """Return the top-k most relevant chunks for the question.

    Deliberately does NOT filter out chunks whose source is already injected in
    full by the always-on summary, though 39% of what it returns is exactly
    that. Filtering them was tried, measured, and reverted, and the reason is
    worth keeping because the change looks free:

    Dropping always-on sources from retrieval does remove the duplication, and
    it saves roughly 157 tokens per pass. But it also makes retrieval
    STRUCTURALLY incapable of surfacing anything whose only home is resume.md or
    roles-open-to.md, which on the eval set moved four questions from "carried
    by retrieval" to "carried by the always-on block alone" and dropped hit@k
    from 1.00 to 0.83.

    That is the wrong direction. The point of shrinking the always-on block (the
    real token win, roughly 3,000 tokens a question) is to have retrieval carry
    that load instead. A filter that bans those documents from retrieval means
    that once the block is compressed, their content is in neither place. The
    two changes are in direct conflict, and this is the cheaper one to give up.

    So the duplication stays until the always-on block shrinks, at which point it
    stops being duplication on its own.
    """
    return vectorstore.similarity_search(question, k=settings.top_k)


def sources_of(retrieved: list[Document]) -> list[str]:
    """Deduplicated, order-preserving list of the sources behind the retrieved chunks."""
    seen: list[str] = []
    for doc in retrieved:
        source = doc.metadata.get("source", "unknown")
        if source not in seen:
            seen.append(source)
    return seen
