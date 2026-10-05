"""Pre-answer the starter questions and save what the judge passed.

The four chips under the composer are the likeliest first click on the page, and
each one costs a full pipeline run: two passes, roughly 5,600 prompt tokens. The
free Groq tier is 200,000 tokens a day, about 33 questions, shared with every
real visitor. Spending a third of that on four questions whose answers do not
change between visitors is the worst trade in the app.

So they are answered once, here, and served from a file. The saved answers are
real output of the real pipeline, judged exactly like a live one: this script
calls ``ChatEngine.answer``, the same method the blocking endpoint calls. It
does not reimplement any part of it, which is the only way the saved answers and
the live ones cannot drift into being different products.

**Rerun this whenever `knowledge/`, the prompts, the models, or the retrieval
knobs change.** You do not have to remember: the output records the fingerprint
it was built under, and `load_starter_answers` drops the whole file when the
fingerprint no longer matches, so a stale cache goes quiet instead of lying.
Verifying that is what `tests/test_starter_answers.py` does, offline and free.

Usage (needs GROQ_API_KEY, and spends roughly 25,000 tokens of the day's budget):

    .venv/bin/python tools/build_starter_answers.py
    .venv/bin/python tools/build_starter_answers.py --dry-run   # free, prints the plan
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chat import ChatEngine, _NOTHING_TO_SAY  # noqa: E402
from app.config import settings  # noqa: E402
from app.fingerprint import corpus_fingerprint  # noqa: E402
from app.profile import STARTER_ANSWERS_PATH, load_profile  # noqa: E402


def _is_refusal(text: str, owner_name: str) -> bool:
    """True when the pipeline declined rather than answered.

    A refusal is a legitimate live answer but a terrible saved one. Saved, it
    becomes permanent: the chip would answer "I can't help with that" forever,
    to every visitor, for a question the owner chose to advertise. Live, the
    same question might well succeed on the next retrieval. So a refusal is left
    out of the file and that chip simply runs live, which is the status quo.
    """
    return text.strip() == _NOTHING_TO_SAY.format(name=owner_name).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the questions and the fingerprint without calling Groq",
    )
    args = parser.parse_args()

    profile = load_profile()
    questions = profile["starter_questions"]
    fingerprint = corpus_fingerprint()

    if not questions:
        print("No starter_questions in profile.json. Nothing to build.")
        return 0

    print(f"fingerprint: {fingerprint}")
    print(f"model:       {settings.drafter_model}")
    for q in questions:
        print(f"  - {q}")

    if args.dry_run:
        print("\n--dry-run: no Groq calls made, nothing written.")
        return 0

    if not settings.groq_api_key:
        print("\nGROQ_API_KEY is not set. Nothing to do.", file=sys.stderr)
        return 1

    engine = ChatEngine()
    entries = []
    for q in questions:
        print(f"\nasking: {q}")
        answer = engine.answer(q)
        if _is_refusal(answer.text, profile["known_as"]):
            print("  declined; leaving this one to run live.")
            continue
        print(f"  {len(answer.text)} chars, sources: {', '.join(answer.sources) or 'none'}")
        entries.append(
            {"question": q, "answer": answer.text, "sources": answer.sources}
        )

    payload = {
        "fingerprint": fingerprint,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.drafter_model,
        "answers": entries,
    }
    STARTER_ANSWERS_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nWrote {len(entries)} answer(s) to {STARTER_ANSWERS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
