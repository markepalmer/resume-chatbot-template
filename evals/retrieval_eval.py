"""Does the right document reach the prompt? Offline, free, no Groq call.

This is the instrument for step 1 of the token work, and it exists before the
fix so that the fix has a baseline to be measured against. Retrieval quality is
the one part of this pipeline that can be scored without spending a single
token: FAISS and the bge-small embedder both run locally, so this can be run a
hundred times a day and belongs in CI.

WHAT IT SCORES

    hit@k       did ANY document that genuinely answers the question arrive?
                The headline number. A question answered by three documents is
                not three times harder than one answered by one.
    recall@k    what fraction of the labelled documents arrived. Secondary,
                because for a question several documents answer, retrieving one
                good one is usually enough.
    MRR         1/rank of the first labelled document, over the deduplicated
                source list. Rewards getting it into the first slot, not the
                fifth.
    must_contain  did the retrieved TEXT contain a required substring. Sharper
                than a source label where one passage of a large document is
                the one that matters. A confidentiality probe that retrieves
                the confidential file scores a hit on source alone even when what came
                back was the summary and the instruction never arrived.

WHAT IT DIAGNOSES

Three counters aimed at the defects the corpus measurement turned up, because
an aggregate score moves too little to show them and they are the actionable
part:

    heading-only slots   chunks that are nothing but a markdown heading. These
                         embed as near-perfect matches for the topic they head
                         and then deliver nothing. '## Skills' is nine chars.
    tiny slots           chunks under 200 chars, which carry little either way.
    duplicated chars     retrieved text drawn from a document whose FULL TEXT is
                         already injected by the always-on summary. Not wrong,
                         but it is a top-k slot spent re-sending what the model
                         has verbatim two thousand characters earlier.

USAGE

    python3 -m evals.retrieval_eval                      # score, print, save
    python3 -m evals.retrieval_eval --compare evals/results/retrieval_BASE.json
    python3 -m evals.retrieval_eval --min-hit 0.9        # CI gate
    python3 -m evals.retrieval_eval --verbose            # per-question detail

Building the index takes a few seconds and the first ever run downloads the
embedding model. Nothing here calls Groq.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.config import ROOT_DIR, settings

QUESTIONS_FILE = ROOT_DIR / "evals" / "questions.yaml"
RESULTS_DIR = ROOT_DIR / "evals" / "results"

# Buckets with no labelled sources. Retrieval always returns its top k, so there
# is no "correctly retrieved nothing" outcome to score; these are refusal tests
# and belong to the groundedness harness.
UNSCORED_BUCKETS = ("out_of_scope", "adversarial")

TINY_CHUNK_CHARS = 200


@dataclass
class QuestionResult:
    id: str
    question: str
    bucket: str
    expected: list[str]
    retrieved: list[str]
    hit: bool
    recall: float
    mrr: float
    must_contain_any: list[str] = field(default_factory=list)
    # Whether the needle was in the RETRIEVED CHUNKS. Not the same as whether
    # the model can answer: the always-on block carries resume.md and
    # roles-open-to.md in full on every call, so a retrieval miss is routinely
    # covered by text that arrived some other way.
    must_contain_met: bool | None = None
    # Whether the needle was anywhere in the CONTEXT THE MODEL ACTUALLY SEES,
    # which is always-on plus retrieved. This is the one that tracks whether an
    # answer is supportable today.
    must_contain_in_prompt: bool | None = None
    # The gap between the two: retrieval missed it and the always-on block is
    # the only reason it is in the prompt. These are precisely the questions
    # that regress when the always-on block is compressed or shrunk, so this
    # count is the risk register for step 4.
    always_on_only: bool = False
    heading_only_slots: int = 0
    tiny_slots: int = 0
    duplicated_chars: int = 0
    retrieved_chars: int = 0


def is_heading_only(text: str) -> bool:
    """True when a chunk is a bare markdown heading and nothing else.

    These are the orphans the current separator list produces: it splits AT
    '\\n## ', so a heading strands alone whenever the block after it does not
    fit in the remaining budget.
    """
    stripped = text.strip()
    if not stripped or "\n" in stripped:
        return False
    return stripped.startswith("#")


def load_questions(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    questions = data.get("questions") or []
    seen: set[str] = set()
    for q in questions:
        qid = q.get("id")
        if not qid:
            raise ValueError(f"a question is missing its id: {q!r}")
        if qid in seen:
            raise ValueError(f"duplicate question id {qid!r}; ids appear in reports")
        seen.add(qid)
    return questions


def score_one(q: dict, retrieved_docs, always_on: str = "") -> QuestionResult:
    expected = list(q.get("expected_sources") or [])
    must = list(q.get("must_contain_any") or [])

    # Deduplicated and order preserving: the same function the pipeline uses to
    # build the sources line, so ranks here are the ranks the site would show.
    from app.retriever import sources_of

    retrieved = sources_of(retrieved_docs)

    found = [s for s in expected if s in retrieved]
    recall = len(found) / len(expected) if expected else 0.0

    mrr = 0.0
    for rank, source in enumerate(retrieved, start=1):
        if source in expected:
            mrr = 1.0 / rank
            break

    blob = "\n".join(d.page_content for d in retrieved_docs).lower()
    ao = always_on.lower()
    must_met = any(s.lower() in blob for s in must) if must else None
    in_prompt = any(s.lower() in blob or s.lower() in ao for s in must) if must else None

    from app.retriever import _ALWAYS_ON_SOURCES

    duplicated = sum(
        len(d.page_content)
        for d in retrieved_docs
        if d.metadata.get("source") in _ALWAYS_ON_SOURCES
    )

    return QuestionResult(
        id=q["id"],
        question=q["question"].strip(),
        bucket=q.get("bucket", "factual"),
        expected=expected,
        retrieved=retrieved,
        hit=bool(found),
        recall=recall,
        mrr=mrr,
        must_contain_any=must,
        must_contain_met=must_met,
        must_contain_in_prompt=in_prompt,
        always_on_only=bool(in_prompt and not must_met),
        heading_only_slots=sum(1 for d in retrieved_docs if is_heading_only(d.page_content)),
        tiny_slots=sum(1 for d in retrieved_docs if len(d.page_content) < TINY_CHUNK_CHARS),
        duplicated_chars=duplicated,
        retrieved_chars=sum(len(d.page_content) for d in retrieved_docs),
    )


def run(questions: list[dict]) -> list[QuestionResult]:
    from app.ingest import build_vectorstore
    from app.retriever import retrieve

    from app.retriever import build_always_on_summary

    store = build_vectorstore()
    always_on = build_always_on_summary()
    results: list[QuestionResult] = []
    for q in questions:
        if q.get("bucket") in UNSCORED_BUCKETS:
            continue
        docs = retrieve(store, q["question"].strip())
        results.append(score_one(q, docs, always_on))
    return results


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate(results: list[QuestionResult]) -> dict:
    must_scored = [r for r in results if r.must_contain_met is not None]
    return {
        "n": len(results),
        "hit_at_k": mean([1.0 if r.hit else 0.0 for r in results]),
        "recall_at_k": mean([r.recall for r in results]),
        "mrr": mean([r.mrr for r in results]),
        "must_contain_rate": mean([1.0 if r.must_contain_met else 0.0 for r in must_scored]),
        "prompt_contain_rate": mean(
            [1.0 if r.must_contain_in_prompt else 0.0 for r in must_scored]
        ),
        "always_on_only": sum(1 for r in must_scored if r.always_on_only),
        "must_contain_n": len(must_scored),
        "heading_only_slots": sum(r.heading_only_slots for r in results),
        "tiny_slots": sum(r.tiny_slots for r in results),
        "duplicated_chars": sum(r.duplicated_chars for r in results),
        "retrieved_chars": sum(r.retrieved_chars for r in results),
    }


def report(results: list[QuestionResult], verbose: bool) -> dict:
    agg = aggregate(results)
    print()
    print("=" * 78)
    print(f"  RETRIEVAL EVAL   top_k={settings.top_k}  chunk={settings.chunk_size}"
          f"/{settings.chunk_overlap}  {agg['n']} scored questions")
    print("=" * 78)

    buckets: dict[str, list[QuestionResult]] = {}
    for r in results:
        buckets.setdefault(r.bucket, []).append(r)

    print(f"\n{'bucket':<14} {'n':>3}  {'hit@k':>7} {'recall':>7} {'MRR':>7}  {'must':>7}")
    print("-" * 60)
    for bucket in sorted(buckets):
        rows = buckets[bucket]
        b = aggregate(rows)
        must = f"{b['must_contain_rate']:.2f}" if b["must_contain_n"] else "   -"
        print(
            f"{bucket:<14} {b['n']:>3}  {b['hit_at_k']:>7.2f} {b['recall_at_k']:>7.2f} "
            f"{b['mrr']:>7.2f}  {must:>7}"
        )
    print("-" * 60)
    must_all = f"{agg['must_contain_rate']:.2f}" if agg["must_contain_n"] else "   -"
    print(
        f"{'OVERALL':<14} {agg['n']:>3}  {agg['hit_at_k']:>7.2f} {agg['recall_at_k']:>7.2f} "
        f"{agg['mrr']:>7.2f}  {must_all:>7}"
    )

    print("\n  slot diagnostics (across all scored questions)")
    total_slots = agg["n"] * settings.top_k
    print(f"    heading-only slots   {agg['heading_only_slots']:4d} of {total_slots}"
          f"   ({agg['heading_only_slots'] / total_slots * 100 if total_slots else 0:.1f}%)"
          f"  <- carry no information")
    print(f"    slots under {TINY_CHUNK_CHARS} chars {agg['tiny_slots']:4d} of {total_slots}"
          f"   ({agg['tiny_slots'] / total_slots * 100 if total_slots else 0:.1f}%)")
    dup_pct = agg["duplicated_chars"] / agg["retrieved_chars"] * 100 if agg["retrieved_chars"] else 0
    print(f"    duplicated chars     {agg['duplicated_chars']:4d} of {agg['retrieved_chars']}"
          f"   ({dup_pct:.1f}%)  <- already in the always-on block verbatim")

    if agg["must_contain_n"]:
        print("\n  where the answer actually comes from"
              f"   ({agg['must_contain_n']} questions with needles)")
        print(f"    in the prompt at all        {agg['prompt_contain_rate']:.2f}"
              "   <- can the model answer today")
        print(f"    carried by retrieval        {agg['must_contain_rate']:.2f}"
              "   <- can retrieval do it alone")
        print(f"    always-on block ONLY        {agg['always_on_only']:4d}"
              "   <- regresses if that block shrinks")

    # A needle missing from the retrieved chunks is NOT a failure on its own.
    # The prompt is always-on plus retrieved, so the always-on block routinely
    # covers a retrieval miss and the answer is fine. Only a needle missing from
    # the whole prompt is a real failure today. The covered ones are listed
    # separately as risk, because they are what breaks when the always-on block
    # is compressed in step 4.
    failures = [r for r in results if not r.hit or r.must_contain_in_prompt is False]
    if failures:
        print(f"\n  {len(failures)} FAILING (fact absent from the prompt entirely):")
        for r in failures:
            if not r.hit:
                print(f"    {r.id}  {r.question}")
                print(f"          no expected source (wanted {r.expected}, got {r.retrieved})")
            if r.must_contain_in_prompt is False:
                print(f"    {r.id}  {r.question}")
                print(f"          {r.must_contain_any} absent from always-on AND retrieval")
    else:
        print("\n  0 failing: every labelled fact is somewhere in the prompt today.")

    at_risk = [r for r in results if r.always_on_only]
    if at_risk:
        print(f"\n  {len(at_risk)} AT RISK (retrieval missed it; only the always-on block")
        print("  is carrying it, so shrinking that block in step 4 drops it):")
        for r in at_risk:
            print(f"    {r.id}  [{r.bucket}]  {r.question}")

    if verbose:
        print("\n  per-question detail")
        for r in results:
            mark = "ok " if r.hit and r.must_contain_met is not False else "FAIL"
            print(f"    [{mark}] {r.id} hit={int(r.hit)} recall={r.recall:.2f} "
                  f"mrr={r.mrr:.2f} heading_slots={r.heading_only_slots} :: {r.question}")

    print("=" * 78)
    return agg


def compare(current: dict, baseline_path: Path) -> None:
    """Print a before/after against a saved run. This is how step 1 gets a number."""
    saved = json.loads(baseline_path.read_text(encoding="utf-8"))
    base = saved["aggregate"]

    print(f"\n  COMPARED TO {baseline_path.name}  (chunk="
          f"{saved.get('config', {}).get('chunk_size')}/"
          f"{saved.get('config', {}).get('chunk_overlap')}, "
          f"top_k={saved.get('config', {}).get('top_k')})")
    print("-" * 68)
    rows = [
        ("hit@k", "hit_at_k", "{:.3f}", 1),
        ("recall@k", "recall_at_k", "{:.3f}", 1),
        ("MRR", "mrr", "{:.3f}", 1),
        ("must_contain", "must_contain_rate", "{:.3f}", 1),
        ("heading-only slots", "heading_only_slots", "{:.0f}", -1),
        ("tiny slots", "tiny_slots", "{:.0f}", -1),
        ("duplicated chars", "duplicated_chars", "{:.0f}", -1),
    ]
    for label, key, fmt, direction in rows:
        before, after = base.get(key, 0), current.get(key, 0)
        delta = after - before
        # An arrow that means "better", not "bigger": fewer orphaned headings is
        # an improvement and a naive +/- would render it as a regression.
        arrow = "  " if delta == 0 else ("up" if delta * direction > 0 else "DOWN")
        print(f"    {label:<22} {fmt.format(before):>9} -> {fmt.format(after):>9}"
              f"  {delta:+9.3f}  {arrow}")
    print("-" * 68)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--questions", type=Path, default=QUESTIONS_FILE)
    ap.add_argument("--compare", type=Path, help="baseline JSON to diff against")
    # Two gates, because they catch different things and the obvious one is the
    # weaker one. hit@k is 1.00 on the current corpus and stays there through
    # every defect this harness found, since a question's document does reach
    # the prompt even when the passage answering it does not. Gate on
    # must_contain if you only gate on one.
    ap.add_argument("--min-hit", type=float, help="exit nonzero if hit@k falls below this")
    ap.add_argument(
        "--min-must", type=float, help="exit nonzero if the must_contain rate falls below this"
    )
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--label", default="", help="tag for the saved results filename")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    questions = load_questions(args.questions)
    scored = [q for q in questions if q.get("bucket") not in UNSCORED_BUCKETS]
    print(f"Loaded {len(questions)} questions ({len(scored)} scored on retrieval, "
          f"{len(questions) - len(scored)} refusal-only).")
    print("Building the index (local, no Groq call)...", flush=True)

    results = run(questions)
    agg = report(results, args.verbose)

    if args.compare:
        compare(agg, args.compare)

    if not args.no_save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tag = f"_{args.label}" if args.label else ""
        out = RESULTS_DIR / f"retrieval{tag}_{stamp}.json"
        out.write_text(
            json.dumps(
                {
                    "utc": stamp,
                    "config": {
                        "chunk_size": settings.chunk_size,
                        "chunk_overlap": settings.chunk_overlap,
                        "top_k": settings.top_k,
                        "embedding_model": settings.embedding_model,
                    },
                    "aggregate": agg,
                    "questions": [asdict(r) for r in results],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nSaved: {out.relative_to(ROOT_DIR)}")

    failed = False
    if args.min_hit is not None and agg["hit_at_k"] < args.min_hit:
        print(f"\nFAIL: hit@k {agg['hit_at_k']:.3f} is below the --min-hit gate of {args.min_hit}",
              file=sys.stderr)
        failed = True
    if args.min_must is not None and agg["must_contain_rate"] < args.min_must:
        print(f"\nFAIL: must_contain {agg['must_contain_rate']:.3f} is below the "
              f"--min-must gate of {args.min_must}", file=sys.stderr)
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
