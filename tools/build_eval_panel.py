#!/usr/bin/env python3
"""Render the Eval panel: findings from measuring the chatbot.

Reads real run files from ``evals/results/`` and writes a self contained SVG to
``static/eval_panel.svg`` plus the table data beside it. Like the career arc and
the results chart, the page inlines the SVG rather than pointing an <img> at it,
so the figure reads this page's CSS variables and follows the theme toggle. The
generator emits classes only and never a literal color.

It imports the groundedness harness to rescore answers, so it needs the repo's
environment. Rerun it whenever FINDINGS changes:

    .venv/bin/python tools/build_eval_panel.py

The server caches both files on first read, so restart it to see a rebuild.

WHY FINDINGS AND NOT CHANGES
----------------------------
The first version plotted three before and after pairs, and it read as a
changelog: two of its three rows were corrections to this project's own
mistakes, which is learning, not evidence. A reviewer is reading for something
else, a moment where measurement changed a decision that reasoning had already
made. So each row is now a claim, and its two tracks are the measurement that
settled it. Row 02 is the clearest case: an obviously correct change, measured,
rejected.

WHY THE FIGURE SHOWS LESS THAN THE TABLE
----------------------------------------
Rewritten 2026-10-05 for readers who have never seen an eval. The first version
showed three findings with two tracks each, in decimals, under metric names
like hit@k, and nobody outside the field could say what it claimed. The figure
now carries two findings with one plain track each, in percentages. Everything
else stays in the table under it ("View the numbers"), which is built from the
same data: finding 01's retrieval track, which scored the fix identically while
the answer eval moved (docs/evals.md has that account), finding 02's recall, and
all of finding 03, which is about the grader rather than the chatbot. A track or
finding with ``"figure": False`` is table only.

WHY GROUNDEDNESS NUMBERS ARE RESCORED HERE
------------------------------------------
Scores are derived; only answers are data. Every answer run is rescored with the
current scorer before a number is taken from it, through the harness's own
``rescore_rows`` so there is one scorer and not two. The single exception is the
"before" side of finding 03, which reads the verdicts as saved, because the stale
score is exactly what that finding is about.

WHY EVERY TRACK CARRIES ITS OWN n
---------------------------------
The rows come from different harnesses and different question subsets (4, 11,
19 and 24 questions), so nothing is comparable between tracks. Each pair is
checked to share its denominator before it is plotted, and the n is printed
beside the label rather than once per row.

WHY COLOR MEANS DIRECTION AND NOT METRIC
----------------------------------------
Tracks are told apart by label and position, never hue, so the figure reads in
grayscale. Color has one job, the direction of the change, on a blue and amber
pair that clears every palette check on both surfaces; green against red is 2.7
dE under deuteranopia. It is redundant in any case: both values are printed.
"""

from __future__ import annotations

import copy
import html
import re
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.groundedness_eval import load_jsonl, rescore_rows  # noqa: E402

RESULTS_DIR = ROOT / "evals" / "results"
OUT_PATH = ROOT / "static" / "eval_panel.svg"
DATA_PATH = ROOT / "static" / "eval_panel.json"

CONFIDENTIAL = ["C01", "C02", "C03", "C04"]

# Each finding is a claim and the two measurements behind it. A track names its
# source by kind:
#   retrieval  two retrieval_eval runs, an aggregate metric from each
#   answers    two groundedness runs aligned on question ids, both rescored
#   rescore    one groundedness run, as saved against as rescored
FINDINGS = [
    {
        "idx": "01",
        "title": "A test caught a rule the chatbot was dropping",
        "detail": "An instruction to keep some work confidential sometimes missed the cut, "
                  "so it is now always included.",
        "verdict": "adopted",
        "tracks": [
            {"label": "Said it was confidential", "measure": "Answer names it confidential",
             "kind": "answers", "metric": "needle", "ids": CONFIDENTIAL,
             "before": "groundedness_AFTER_STEP1_MERGED.jsonl",
             "after": "groundedness_AFTER_ALWAYSON_20260903T180825Z.jsonl"},
            {"label": "Search test", "measure": "Retrieval eval, must contain",
             "kind": "retrieval", "metric": "must_contain_rate", "figure": False,
             "before": "retrieval_BEFORE_ALWAYSON_20260903T180615Z.json",
             "after": "retrieval_AFTER_ALWAYSON_20260903T180705Z.json"},
        ],
    },
    {
        "idx": "02",
        "title": "Saving tokens cost facts",
        "detail": "Trimming repeated text saved 157 tokens a question but lost correct sources, "
                  "so it was undone.",
        "verdict": "rejected",
        "tracks": [
            {"label": "Found a right source", "measure": "hit@k", "kind": "retrieval",
             "metric": "hit_at_k",
             "before": "retrieval_AFTER_STEP1_20260902T192957Z.json",
             "after": "retrieval_AFTER_STEP2_20260902T193115Z.json"},
            {"label": "Share of right sources found", "measure": "recall@k", "kind": "retrieval",
             "metric": "recall_at_k", "figure": False,
             "before": "retrieval_AFTER_STEP1_20260902T192957Z.json",
             "after": "retrieval_AFTER_STEP2_20260902T193115Z.json"},
        ],
    },
    {
        "idx": "03",
        "title": "The grader was wrong, not the chatbot",
        "detail": "It marked curly apostrophes and reworded answers as misses, "
                  "so every answer was regraded.",
        "verdict": "corrected",
        "figure": False,
        "tracks": [
            {"label": "Refused when it should", "measure": "Refused when it should, as scored then rescored",
             "kind": "rescore", "metric": "refusal",
             "run": "groundedness_BEFORE_STEP1_20260902T190112Z.jsonl"},
            {"label": "Right fact in the answer", "measure": "Fact reached the answer, as scored then rescored",
             "kind": "rescore", "metric": "needle",
             "run": "groundedness_BEFORE_STEP1_20260902T190112Z.jsonl"},
        ],
    },
]

# ---------------------------------------------------------------------------
# Geometry, in viewBox units; the stylesheet's font sizes are on the same scale.
# A row is the claim and its detail, wrapped, on the left, and on the right the
# track's label level with the claim and the track itself under it. The label
# sits above its track rather than beside it because plain labels are longer
# than the metric names they replaced.
# ---------------------------------------------------------------------------
W = 980
PAD_X = 34
TEXT_X = PAD_X + 44  # clears the two digit index
LABEL_X = 600        # left edge of the right column; the detail wraps before it
X0, X1 = 600, 800    # the track itself
DETAIL_CHARS = 66    # wrap width of the detail, about 450 units at 13.5px
VAL_X = W - PAD_X    # values are printed right aligned, clear of every mark
ROW_H = 92           # a row with a one line detail; each extra line adds LINE_H
LINE_H = 20
TITLE_DY = 36
DETAIL_DY = 62
TRACK_RAISE = 5      # lifts a track from its text baseline to the text's middle
DOT_BEFORE_R = 4.5
DOT_AFTER_R = 6

# The shared value axis. Every plotted value is a rate at or above 0.604, and a
# zero baseline would squash the differences that matter into a few pixels.
V_MIN, V_MAX = 0.5, 1.0

FOOT = "Hollow mark before, solid mark after, on a scale from 50% to 100%."


def wrap(text: str, width: int = DETAIL_CHARS) -> list[str]:
    """Greedy word wrap. The build has no font metrics, so width is characters."""
    lines: list[str] = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def path(name: str) -> Path:
    p = RESULTS_DIR / name
    if not p.exists():
        raise SystemExit(
            f"missing run file: {p}\n"
            "The figure is generated from real runs, so a renamed or deleted "
            "result file is a hard error rather than a gap in the chart."
        )
    return p


def rate(rows, metric: str) -> tuple[int, float]:
    """(n scored, rate) for "needle" or "refusal" over answered rows."""
    field = "needle_met" if metric == "needle" else "refused"
    scored = [getattr(r, field) for r in rows if r.ok and getattr(r, field) is not None]
    if not scored:
        raise SystemExit(f"no rows scored for {metric}; the track would plot nothing")
    return len(scored), sum(scored) / len(scored)


def resolve(track: dict) -> tuple[int, float, float]:
    """Return (n, before, after) for one track, refusing any mismatched pair."""
    kind = track["kind"]
    if kind == "retrieval":
        b = json.loads(path(track["before"]).read_text())["aggregate"]
        a = json.loads(path(track["after"]).read_text())["aggregate"]
        if b["n"] != a["n"]:
            raise SystemExit(f"{track['label']}: n is {b['n']} before and {a['n']} after. "
                             "That is two rulers, not a comparison.")
        return b["n"], b[track["metric"]], a[track["metric"]]

    if kind == "answers":
        ids = set(track["ids"])
        sides = []
        for name in (track["before"], track["after"]):
            rows = [r for r in load_jsonl(path(name)) if r.id in ids]
            if {r.id for r in rows} != ids:
                raise SystemExit(f"{name} is missing some of {sorted(ids)}; "
                                 "an unaligned pair measures the question set, not the change.")
            sides.append(rate(rescore_rows(rows), track["metric"]))
        (nb, b), (na, a) = sides
        if nb != na:
            raise SystemExit(f"{track['label']}: {nb} scored before and {na} after.")
        return nb, b, a

    if kind == "rescore":
        rows = load_jsonl(path(track["run"]))
        n_saved, as_saved = rate(rows, track["metric"])
        n_now, rescored = rate(rescore_rows(copy.deepcopy(rows)), track["metric"])
        if n_saved != n_now:
            raise SystemExit(f"{track['label']}: rescoring changed which rows are scored.")
        return n_now, as_saved, rescored

    raise SystemExit(f"unknown track kind: {kind}")


def resolved() -> list[dict]:
    return [{**f, "tracks": [{**t, "values": resolve(t)} for t in f["tracks"]]} for f in FINDINGS]


def vx(value: float) -> float:
    t = (value - V_MIN) / (V_MAX - V_MIN)
    return X0 + max(0.0, min(1.0, t)) * (X1 - X0)


def build(findings: list[dict]) -> str:
    shown = [f for f in findings if f.get("figure", True)]
    rows = []
    for f in shown:
        tracks = [t for t in f["tracks"] if t.get("figure", True)]
        if len(tracks) != 1:
            raise SystemExit(f"finding {f['idx']} shows {len(tracks)} tracks in the figure; "
                             "the layout holds one, the rest belong in the table.")
        rows.append((f, tracks[0], wrap(f["detail"])))

    tops = []
    y = 0
    for _, _, lines in rows:
        tops.append(y)
        y += ROW_H + LINE_H * (len(lines) - 1)
    height = y + 34

    out: list[str] = []
    a = out.append

    a(f'<svg class="eval-panel" xmlns="http://www.w3.org/2000/svg" '
      f'viewBox="0 0 {W} {height}" role="img" '
      f'aria-label="Two findings from testing the chatbot: a confidentiality rule a test caught, '
      f'and a token saving that was undone because it lost facts">')

    for k, ((f, track, lines), top) in enumerate(zip(rows, tops)):
        if k:
            a(f'<line class="row-rule" x1="{PAD_X}" y1="{top}" x2="{W - PAD_X}" y2="{top}"/>')
        a(f'<text class="idx" x="{PAD_X}" y="{top + TITLE_DY}">{f["idx"]}</text>')
        a(f'<text class="claim" x="{TEXT_X}" y="{top + TITLE_DY}">{esc(f["title"])}</text>')
        a(f'<text class="row-detail" x="{TEXT_X}" y="{top + DETAIL_DY}">'
          + "".join(f'<tspan x="{TEXT_X}" dy="{0 if n == 0 else LINE_H}">{esc(line)}</tspan>'
                    for n, line in enumerate(lines))
          + "</text>")

        n, b, aft = track["values"]
        ty = top + DETAIL_DY - TRACK_RAISE
        delta = aft - b
        cls = "is-flat" if abs(delta) < 5e-4 else ("is-up" if delta > 0 else "is-down")

        a(f'<text class="track-label" x="{LABEL_X}" y="{top + TITLE_DY}">{esc(track["label"])}'
          f'<tspan class="n" dx="7">n={n}</tspan></text>')
        a(f'<g class="cell {cls}">')
        a(f'<line class="track" x1="{X0}" y1="{ty}" x2="{X1}" y2="{ty}"/>')
        # A flat track draws the after mark alone: a hollow dot hidden under
        # a solid one would claim a before value the reader cannot see.
        if cls != "is-flat":
            a(f'<line class="move" x1="{vx(b):.1f}" y1="{ty}" x2="{vx(aft):.1f}" y2="{ty}"/>')
            a(f'<circle class="dot-before" cx="{vx(b):.1f}" cy="{ty}" r="{DOT_BEFORE_R}"/>')
        a(f'<circle class="dot-after" cx="{vx(aft):.1f}" cy="{ty}" r="{DOT_AFTER_R}"/>')
        a(f'<text class="val" x="{VAL_X}" y="{top + DETAIL_DY}" text-anchor="end">{pct(b)}'
          f'<tspan class="arrow"> → </tspan><tspan class="val-after">{pct(aft)}</tspan></text>')
        a(f'<title>{esc(track["measure"])}: {pct(b)} to {pct(aft)}, n={n}</title>')
        a('</g>')

    a(f'<text class="fig-foot" x="{PAD_X}" y="{height - 12}">{esc(FOOT)}</text>')
    a('</svg>')
    return "\n".join(out) + "\n"


def measured_for() -> str:
    """Whose assistant these numbers describe, read from profile.json.

    Stamped into the data file so app/profile.py can drop the whole section when
    it does not match the profile being served. The generated pair is committed,
    so without this a fork rendered these findings as its own evidence. Empty on
    any failure, which fails closed: an unstamped file matches no profile.
    """
    try:
        profile = json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return profile.get("name", "") if isinstance(profile, dict) else ""


def headline() -> dict:
    """The newest retrieval run, for the one number the section's note states.

    Newest by the UTC stamp in the file name rather than by mtime, which a fresh
    checkout resets. Stamped into the data file so the sentence on the page is
    regenerated with the figure instead of typed into the template, where it
    would go stale the first time the question set grew (it went from 24 to 28
    while the highlight card still said 24).
    """
    runs = [(m.group(1), p) for p in RESULTS_DIR.glob("retrieval_*.json")
            if (m := re.search(r"(\d{8}T\d{6}Z)\.json$", p.name))]
    if not runs:
        return {}
    _, newest = max(runs)
    agg = json.loads(newest.read_text())["aggregate"]
    return {"n": agg["n"], "found": round(agg["hit_at_k"] * agg["n"]), "run": newest.name}


def build_data(findings: list[dict]) -> dict:
    """The same values the SVG plots, for the table the page renders under it.

    Emitted from this generator rather than written into the template so the
    figure and its table cannot disagree. The table is the figure under 760px and
    the no JS path, so it is the copy that must not rot.
    """
    return {"measured_for": measured_for(), "headline": headline(), "rows": [
        {
            "idx": f["idx"],
            "title": f["title"],
            "detail": f["detail"],
            "verdict": f["verdict"],
            "metrics": [
                # The short label, as on the figure. The long form made the table
                # twice as tall at 390px, where the table is the figure; it lives
                # in each mark's tooltip instead.
                {"label": t["label"], "n": t["values"][0],
                 "before": round(t["values"][1], 3), "after": round(t["values"][2], 3)}
                for t in f["tracks"]
            ],
        }
        for f in findings
    ]}


if __name__ == "__main__":
    findings = resolved()
    OUT_PATH.write_text(build(findings))
    DATA_PATH.write_text(json.dumps(build_data(findings), indent=2) + "\n")
    print(f"wrote {OUT_PATH.relative_to(ROOT)} and {DATA_PATH.relative_to(ROOT)}")
