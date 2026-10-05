"""A hash of everything that decides what the pipeline will answer.

Its own module, with nothing heavy imported, and that is the whole reason it is
not in ``app/ingest.py`` where it otherwise belongs. The page route reads it (via
``load_starter_answers``) on every render, and importing ``ingest`` pulls in FAISS
and the embedding stack. CLAUDE.md: nothing that only the chatbot needs may be
required to render the page, because on a scale-to-zero platform the page is what
most visitors came for.
"""

import hashlib
import json

from . import prompts
from .config import PROFILE_PATH, settings


def _always_on_sections() -> list:
    """The always_on_sections list, read straight from profile.json.

    Read here rather than through app/profile.py because that module imports
    this one. Cheap: one small JSON file, and this module's whole reason for
    existing is that the page route can import it without pulling anything
    heavy in behind it.
    """
    if not PROFILE_PATH.exists():
        return []
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8")).get("always_on_sections", [])
    except (json.JSONDecodeError, OSError):
        return []


def corpus_fingerprint() -> str:
    """Hash the corpus, the retrieval knobs, the prompts, and the model names.

    This exists for the pre-answered starter questions in
    ``static/starter_answers.json``. Those are real pipeline output, judged and
    saved, and the danger of saved output is that it outlives the thing it was
    derived from: edit a line in ``knowledge/`` and the page keeps serving
    yesterday's answer, confidently and with citations, until somebody notices.

    So the file records the fingerprint it was built under and the loader
    refuses it when the two disagree. The failure mode becomes "the cache goes
    quiet and questions run live again", which costs tokens, rather than "the
    site states something that is no longer true", which costs the job.

    Everything hashed is an input to the answer. The embedding model is in here
    because changing it changes which chunks come back, and both model names are
    because the same question put to a different model is a different answer.
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
        "top_k": settings.top_k,
        "embedding_model": settings.embedding_model,
        "drafter_model": settings.drafter_model,
        "judge_model": settings.judge_model,
        "prompts": [prompts.DRAFTER_SYSTEM, prompts.JUDGE_SYSTEM, prompts.JUDGE_USER],
        # Which document sections are injected on every call. An input to the
        # answer exactly as the prompts are, and it became one that can change
        # without a code edit when it moved into profile.json, so a saved
        # answer must not survive it changing.
        "always_on_sections": _always_on_sections(),
    }
    blob = json.dumps(parts, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]
