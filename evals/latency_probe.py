"""Step zero of the model-selection work: is chat latency queue time or compute time?

The premise behind "drop to a smaller model" is that the wait is the model
thinking. The README's own numbers argue otherwise: five *identical* requests
returned in 0.60 s, 6.82 s, 9.69 s, 12.72 s and 5.57 s. A 20x spread on
identical input is not how compute behaves. It is how a queue behaves.

That distinction decides whether any of the model work is worth doing, because a
smaller model shortens the compute term and does nothing at all to the queue
term. So this probe runs before the eval set exists, and it does not measure
quality at all.

It does not have to infer the answer. Groq returns the split itself, per call:

    queue_time       waiting for a slot          <- not affected by model size
    prompt_time      processing the input        <- scales with context length
    completion_time  generating the output       <- what a smaller model shrinks

langchain-groq drops those three fields (it keeps only the token counts, see
``_convert_chunk_to_message_chunk``), so this talks to the ``groq`` SDK
directly. That is fine here and would not be in app/: this file never ships. The
Dockerfile copies app/, knowledge/, templates/, static/ and profile.json, so
evals/ is dev tooling on the same footing as tests/, and its dependency is
declared in requirements-dev.txt rather than requirements.txt.

WHAT IT MEASURES

The drafter pass only, and blocking, because that is where the perceived wait
lives. The drafter runs to completion before a single token reaches the browser,
so the visitor waits out all of it; the judge is streamed, so only its
time-to-first-token is felt. ``--pass judge`` measures that one instead.

METHOD

Models are interleaved, not run in blocks. Groq's queue depth varies minute to
minute, so running ten calls of 120b and then ten of 20b measures the two
minutes as much as the two models, and whichever went second gets whatever the
queue was doing then. Round-robin puts both models in the same minutes.

The prompt is the real one: the real retriever over the real corpus, formatted
by the real ``app.prompts.DRAFTER_SYSTEM``. Nothing here restates a prompt, so
this cannot drift from what production sends. Retrieval runs once and the
context is reused for every call, which is faithful (retrieval is deterministic
for a fixed question) and costs no quota.

QUOTA

Every call spends real free-tier budget against the same allowance the live site
answers visitors from. The run prints its planned request count and waits for
confirmation. Keep --runs modest.

USAGE

    python3 -m evals.latency_probe                      # 10 runs x 2 models
    python3 -m evals.latency_probe --runs 6
    python3 -m evals.latency_probe --models openai/gpt-oss-120b,openai/gpt-oss-20b
    python3 -m evals.latency_probe --pass judge
    python3 -m evals.latency_probe --yes                # skip the confirmation
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app import prompts
from app.config import ROOT_DIR, settings

RESULTS_DIR = ROOT_DIR / "evals" / "results"

# The question every run is built from, unless --question names another: the
# first starter question in profile.json. It is what visitors actually click,
# so the context is representative rather than a worst case, and reading it
# from the profile lets a fork probe its own corpus without editing this file.
def default_question() -> str:
    from app.profile import load_profile

    starters = load_profile().get("starter_questions") or []
    return starters[0] if starters else "What do you work on?"

# The two models the selection question is actually between. Verified present in
# GET /openai/v1/models on 2026-09-02; llama-3.1-8b-instant and llama-3.3-70b are
# both gone from the catalogue, so neither is a fallback any more.
DEFAULT_MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b")

# A stand-in draft for --pass judge. The judge edits a draft it is handed, so it
# needs one; this is shaped like a real drafter answer and deliberately contains
# one unsupported claim ("a team of six") so the pass has something to do rather
# than waving through text it agrees with.
CANNED_DRAFT_TEMPLATE = (
    "{name}'s capstone was an object-grounded retrieval system built with a team "
    "of six. It combined a vector index with a re-ranking stage and was evaluated on "
    "retrieval accuracy."
)


@dataclass
class Call:
    """One completed request. Times in seconds, as Groq reports them."""

    model: str
    run: int
    ok: bool
    wall: float
    queue_time: float | None = None
    prompt_time: float | None = None
    completion_time: float | None = None
    total_time: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    ttft: float | None = None
    error: str | None = None


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile.

    Deliberately not an interpolating one. At the sample sizes this probe can
    afford, interpolation invents precision between two observations that a
    tenth call could move; the nearest rank is at least a number that actually
    happened.
    """
    if not values:
        return float("nan")
    ordered = sorted(values)
    # ceil, not round: round() is banker's rounding in Python, which sends p90
    # of ten samples to rank 10 (the max) instead of rank 9.
    rank = max(1, min(len(ordered), math.ceil(pct / 100.0 * len(ordered))))
    return ordered[rank - 1]


def build_context(question: str) -> str:
    """The real retrieved context for this question, via the real code path.

    Builds the FAISS index, so this is a few seconds of local work and the first
    ever run downloads the embedding model. No Groq call and no quota spent.
    """
    from app import retriever
    from app.ingest import build_vectorstore

    store = build_vectorstore()
    retrieved = retriever.retrieve(store, question)
    return retriever.format_context(retrieved, retriever.build_always_on_summary())


def messages_for(which: str, question: str, context: str) -> list[dict[str, str]]:
    """The exact message list the named pass sends in production.

    The owner's name comes from profile.json, not a literal, for the same reason
    app/ may not hardcode one: a fork of this repo has to be able to run its own
    probe without editing it.
    """
    from app.profile import load_profile

    name = load_profile()["known_as"]
    if which == "drafter":
        return [
            {"role": "system", "content": prompts.DRAFTER_SYSTEM.format(name=name, context=context)},
            {"role": "user", "content": question},
        ]
    return [
        {"role": "system", "content": prompts.JUDGE_SYSTEM.format(name=name, context=context)},
        {
            "role": "user",
            "content": prompts.JUDGE_USER.format(
                question=question, draft=CANNED_DRAFT_TEMPLATE.format(name=name)
            ),
        },
    ]


def _usage_fields(usage) -> dict:
    """Pull the timing and token split off a Groq usage object."""
    if usage is None:
        return {}
    details = getattr(usage, "completion_tokens_details", None)
    return {
        "queue_time": getattr(usage, "queue_time", None),
        "prompt_time": getattr(usage, "prompt_time", None),
        "completion_time": getattr(usage, "completion_time", None),
        "total_time": getattr(usage, "total_time", None),
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "reasoning_tokens": getattr(details, "reasoning_tokens", None) if details else None,
    }


def one_call(client, model: str, run: int, which: str, msgs: list[dict], stream: bool) -> Call:
    """Fire one request and record what it cost in time."""
    max_tokens = settings.judge_max_tokens if which == "judge" else settings.drafter_max_tokens
    temperature = settings.judge_temperature if which == "judge" else settings.drafter_temperature

    started = time.perf_counter()
    try:
        if not stream:
            resp = client.chat.completions.create(
                model=model, messages=msgs, temperature=temperature, max_tokens=max_tokens
            )
            wall = time.perf_counter() - started
            return Call(model=model, run=run, ok=True, wall=wall, **_usage_fields(resp.usage))

        # Streaming: the number that matters is time to the first *visible*
        # token, so empty deltas do not count as arrival.
        ttft = None
        usage = None
        for chunk in client.chat.completions.create(
            model=model,
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        ):
            if ttft is None and chunk.choices and (chunk.choices[0].delta.content or ""):
                ttft = time.perf_counter() - started
            # Groq carries usage on the final chunk, under x_groq.
            x_groq = getattr(chunk, "x_groq", None)
            if x_groq is not None and getattr(x_groq, "usage", None) is not None:
                usage = x_groq.usage
        wall = time.perf_counter() - started
        return Call(model=model, run=run, ok=True, wall=wall, ttft=ttft, **_usage_fields(usage))
    except Exception as exc:  # noqa: BLE001 - a failed call is a datapoint, not a crash
        return Call(
            model=model,
            run=run,
            ok=False,
            wall=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )


def summarize(calls: list[Call], models: tuple[str, ...], which: str) -> None:
    """Print the per-model table and the verdict the probe exists to produce."""
    print()
    print("=" * 78)
    print(f"  {which.upper()} PASS")
    print("=" * 78)

    medians: dict[str, dict[str, float]] = {}

    for model in models:
        rows = [c for c in calls if c.model == model and c.ok]
        failed = [c for c in calls if c.model == model and not c.ok]
        print(f"\n{model}   ({len(rows)} ok, {len(failed)} failed)")
        if not rows:
            for f in failed[:3]:
                print(f"    error: {f.error}")
            continue

        wall = [c.wall for c in rows]
        print(
            f"    wall clock      min {min(wall):6.2f}s   p50 {percentile(wall, 50):6.2f}s   "
            f"p90 {percentile(wall, 90):6.2f}s   max {max(wall):6.2f}s"
        )
        if which == "judge":
            ttfts = [c.ttft for c in rows if c.ttft is not None]
            if ttfts:
                print(
                    f"    time to token   min {min(ttfts):6.2f}s   p50 {percentile(ttfts, 50):6.2f}s   "
                    f"p90 {percentile(ttfts, 90):6.2f}s   max {max(ttfts):6.2f}s"
                )

        def med(attr: str) -> float:
            vals = [getattr(c, attr) for c in rows if getattr(c, attr) is not None]
            return statistics.median(vals) if vals else float("nan")

        q, p, comp = med("queue_time"), med("prompt_time"), med("completion_time")
        server = (q or 0) + (p or 0) + (comp or 0)

        # Wall clock minus everything Groq admits to. This is the number that
        # matters most and the one the first version of this probe did not
        # print, which is how it managed to call a 21 s median "compute-bound"
        # off a 0.78 s server-side split. Time that the server does not account
        # for was spent in the client: almost always the SDK sleeping off a 429
        # under its default max_retries=2, which is invisible because the retry
        # then succeeds and the usage returned describes only that last attempt.
        unaccounted = [
            c.wall - c.total_time for c in rows if c.total_time is not None
        ]
        med_unacc = statistics.median(unaccounted) if unaccounted else float("nan")

        medians[model] = {
            "queue": q,
            "prompt": p,
            "completion": comp,
            "wall_p50": percentile(wall, 50),
            "unaccounted": med_unacc,
        }
        print(f"    ---- median server-side split ({server:.2f}s of it) ----")
        print(f"    queue           {q:6.2f}s   {(q / server * 100) if server else 0:5.1f}%   (model size does not move this)")
        print(f"    prompt          {p:6.2f}s   {(p / server * 100) if server else 0:5.1f}%   (scales with context length)")
        print(f"    completion      {comp:6.2f}s   {(comp / server * 100) if server else 0:5.1f}%   (this is what a smaller model shrinks)")

        if unaccounted:
            print(
                f"    UNACCOUNTED     {med_unacc:6.2f}s   wall clock the server does not "
                f"explain (max {max(unaccounted):.2f}s)"
            )

        qs = [c.queue_time for c in rows if c.queue_time is not None]
        if qs:
            print(f"    queue spread    min {min(qs):6.2f}s   max {max(qs):6.2f}s")

        toks = [c.completion_tokens for c in rows if c.completion_tokens is not None]
        reas = [c.reasoning_tokens for c in rows if c.reasoning_tokens is not None]
        if toks:
            line = f"    completion tok  median {statistics.median(toks):.0f}"
            if reas and any(reas):
                line += f"   of which reasoning {statistics.median(reas):.0f}"
            print(line)
        pt = [c.prompt_tokens for c in rows if c.prompt_tokens is not None]
        if pt:
            print(f"    prompt tok      median {statistics.median(pt):.0f}")

    # The verdict. This is the whole point of the probe, so it says it in words
    # rather than leaving it to be read out of the table.
    print()
    print("-" * 78)
    if len(medians) < 1:
        print("  No successful calls; nothing to conclude.")
        print("-" * 78)
        return

    big = medians.get(models[0])
    if big:
        server = big["queue"] + big["prompt"] + big["completion"]
        unacc = big["unaccounted"]
        queue_share = (big["queue"] / server * 100) if server else 0.0
        # NaN-safe: unaccounted is nan when no call reported total_time.
        rate_limited = unacc == unacc and unacc > max(2.0, server)

        # Checked before queue and compute, because when it is present it
        # dominates both, and because neither a smaller model nor a shorter
        # queue does anything about it. The threshold is generous: a second or
        # more of wall clock the server will not account for is not jitter.
        if rate_limited:
            print(f"  VERDICT: rate-limited, not compute-bound. {unacc:.1f}s of the median")
            print(f"  {models[0]} call is wall clock the server does not account for,")
            print(f"  against {server:.2f}s that it does. That gap is the SDK sleeping off a")
            print("  429 under its default max_retries=2 and then succeeding, so the usage")
            print("  it reports describes only the retry that worked.")
            print()
            print("  A smaller model does not fix this. Prompt tokens dominate the spend")
            print("  and do not depend on model size. The lever is prompt size.")
        elif queue_share > 50:
            print(f"  VERDICT: queue-bound. {queue_share:.0f}% of {models[0]}'s server time is")
            print("  queue wait, which a smaller model does not shorten. Expect a modest")
            print("  win at best from switching, and check p90 rather than p50.")
        elif queue_share < 20:
            print(f"  VERDICT: compute-bound. Only {queue_share:.0f}% of server time is queue,")
            print("  so completion time dominates and a smaller model should help roughly")
            print("  in proportion to how much faster it generates.")
        else:
            print(f"  VERDICT: mixed. {queue_share:.0f}% queue. A smaller model helps, but")
            print("  less than the wall-clock difference between the two suggests.")

    if len(medians) >= 2 and all(m in medians for m in models[:2]):
        a, b = medians[models[0]], medians[models[1]]
        if a["wall_p50"] and b["wall_p50"]:
            delta = a["wall_p50"] - b["wall_p50"]
            pct = delta / a["wall_p50"] * 100
            print()
            print(f"  {models[1]} is {delta:+.2f}s ({pct:+.0f}%) on median wall clock vs {models[0]}.")
            if big and rate_limited:
                # Under throttling this comparison is close to meaningless, and
                # reading it as a model-speed difference is the exact mistake
                # this probe exists to prevent. Each model has its own token
                # bucket, so whichever one tripped its limit first absorbs the
                # backoff and the other looks fast by contrast.
                print("  TREAT THAT NUMBER AS NOISE: the models have separate token buckets,")
                print("  so this is mostly which one tripped its limit first. The honest")
                print("  comparison under throttling is server-side completion time:")
                ca, cb = a["completion"], b["completion"]
                print(f"    {models[0]}: {ca:.2f}s     {models[1]}: {cb:.2f}s     delta {ca - cb:+.2f}s")
            else:
                print("  Whether that is worth any accuracy cost is what the eval set decides.")
    print("-" * 78)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=10, help="calls per model (default 10)")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS), help="comma-separated model ids")
    ap.add_argument("--question", default=None,
                    help="default: the first starter question in profile.json")
    ap.add_argument("--pass", dest="which", choices=("drafter", "judge"), default="drafter")
    ap.add_argument("--yes", action="store_true", help="skip the quota confirmation")
    ap.add_argument(
        "--replay",
        metavar="JSON",
        help="re-analyse a saved run instead of making calls. Spends no quota, and "
        "means a corrected verdict can be applied to numbers already collected.",
    )
    args = ap.parse_args()
    if args.question is None:
        args.question = default_question()

    if args.replay:
        saved = json.loads(Path(args.replay).read_text(encoding="utf-8"))
        summarize(
            [Call(**c) for c in saved["calls"]],
            tuple(saved["models"]),
            saved["pass"],
        )
        return 0

    models = tuple(m.strip() for m in args.models.split(",") if m.strip())
    if not models:
        print("No models given.", file=sys.stderr)
        return 2

    key = settings.groq_api_key or os.environ.get("GROQ_API_KEY", "")
    if not key:
        print("GROQ_API_KEY is not set. This probe makes real Groq calls.", file=sys.stderr)
        return 2

    total = args.runs * len(models)
    print(f"Probe: {args.which} pass, {args.runs} runs x {len(models)} models = {total} Groq calls.")
    print(f"Models: {', '.join(models)}")
    print(f"Question: {args.question!r}")
    print()
    print(f"  This spends real free-tier quota, from the same allowance the live site")
    print(f"  answers visitors from. {total} calls.")
    if not args.yes:
        try:
            if input("  Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1

    print("\nBuilding the real retrieval context (local, no quota)...", flush=True)
    context = build_context(args.question)
    msgs = messages_for(args.which, args.question, context)
    approx_ctx = sum(len(m["content"]) for m in msgs)
    print(f"Context built: {approx_ctx} chars of prompt.\n")

    from groq import Groq

    client = Groq(api_key=key)
    stream = args.which == "judge"

    calls: list[Call] = []
    # Round-robin, so both models sit in the same queue conditions.
    for run in range(1, args.runs + 1):
        for model in models:
            result = one_call(client, model, run, args.which, msgs, stream)
            calls.append(result)
            if result.ok:
                extra = f" ttft {result.ttft:.2f}s" if result.ttft is not None else ""
                q = f" queue {result.queue_time:.2f}s" if result.queue_time is not None else ""
                print(f"  run {run:2d}  {model:<26} {result.wall:6.2f}s{q}{extra}", flush=True)
            else:
                print(f"  run {run:2d}  {model:<26} FAILED  {result.error}", flush=True)

    summarize(calls, models, args.which)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"latency_{args.which}_{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "utc": stamp,
                "pass": args.which,
                "question": args.question,
                "models": list(models),
                "runs": args.runs,
                "prompt_chars": approx_ctx,
                "calls": [asdict(c) for c in calls],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nRaw results: {out.relative_to(ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
