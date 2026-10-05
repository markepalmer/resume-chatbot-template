#!/usr/bin/env python3
"""Render the Results chart: measured numbers from one project, plotted.

Reads the ``results`` block of ``profile.json`` and writes a self contained SVG
to ``static/results_chart.svg``. Like the career arc, the page inlines that file
rather than pointing an <img> at it, so the chart reads this page's CSS
variables and follows the theme toggle. The generator emits classes only and
never a literal color.

The mark is chosen from the data rather than configured:

* every point carries an ``x`` and a ``y``  ->  scatter, because two numbers in
  tension is a tradeoff and the reader's job is to see the shape of it. An
  optional frontier traces the points nothing else beats on both axes.
* points carry only an ``x``               ->  ranked horizontal bars, because
  one number per item is a comparison of magnitudes and bars are what magnitude
  reads as.

That split is the whole reason the block is shaped the way it is. A fork of this
site will usually have "we measured one thing across N options", which is bars;
the two axis case is rarer and more interesting, and neither should require the
forker to know which chart they want. ``scripts/setup_profile.py`` asks how many
numbers per item and writes the matching shape.

Every point is labeled on the mark itself, so identity never depends on color.
That matters twice over: it is what makes the chart readable in grayscale or
with a color vision deficiency, and it is what lets the two hues stay purely
emphatic (one point may set ``highlight`` to mark the option that was chosen)
instead of carrying meaning a legend would have to decode.

Run it from anywhere:

    python3 tools/build_results_chart.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "profile.json"
OUT_PATH = ROOT / "static" / "results_chart.svg"

# ---------------------------------------------------------------------------
# Geometry. The viewBox is fixed and the page scales it, so every number here is
# in viewBox units and the font sizes in the stylesheet are on the same scale.
# ---------------------------------------------------------------------------
W, H = 980, 505
M_TOP, M_BOTTOM = 40, 78
M_LEFT, M_RIGHT = 104, 150  # right margin is wide: point labels sit outside the mark

DOT_R = 9  # >= 8, so the mark is a target and not a speck
DOT_R_HI = 12
BAR_H = 30
BAR_GAP = 14
LABEL_DX = 17  # gap between a dot and its label
BAR_LABEL_PX = 16  # must track .results-chart .bar-label font-size
BAR_GUTTER_MAX = 340  # a label longer than this is truncated by the viewBox, not squeezed
BAR_VALUE_ROOM = 96  # right side of a bar chart holds a number, never a name

# A scatter may start its axes off zero, which is legitimate for two measured
# quantities but only while the padding is visible as padding. Bars may never:
# a truncated bar lies about ratio, so BAR mode pins the axis to zero.
PAD_FRAC = 0.12


def fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def num(v: float) -> str:
    """Format a data value for display, keeping decimals only where they matter."""
    if v == int(v):
        return str(int(v))
    return f"{v:.3f}".rstrip("0").rstrip(".")


def nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    """Round tick values covering [lo, hi], stepped 1 / 2 / 2.5 / 5 per decade."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(target, 2)
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= mult * mag:
            step = mult * mag
            break
    else:  # pragma: no cover - the 10 * mag branch always catches
        step = 10 * mag
    start = math.floor(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 0.001:
        # Re-round: repeated addition of 2.5e-1 style steps drifts into 0.7500001
        ticks.append(round(v, 10))
        v += step
    return [t for t in ticks if lo - step * 0.001 <= t <= hi + step * 0.001]


def pareto(points: list[dict]) -> list[dict]:
    """The points no other point beats on both axes, left to right.

    Both axes are treated as "higher is better", which is why ``results`` asks
    for measurements already oriented that way (FPS, accuracy) rather than for
    costs. A latency study should be entered as its own inverse, or the frontier
    turned off.
    """
    ordered = sorted(points, key=lambda p: (-p["x"], -p["y"]))
    best = -math.inf
    keep = []
    for p in ordered:
        if p["y"] > best:
            keep.append(p)
            best = p["y"]
    return sorted(keep, key=lambda p: p["x"])


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------
def axis_group(
    ticks_x: list[float],
    ticks_y: list[float],
    sx,
    sy,
    x_label: str,
    y_label: str,
) -> list[str]:
    """Grid, ticks and axis titles. Shared by both marks so they stay identical."""
    out = []
    x0, x1 = M_LEFT, W - M_RIGHT
    y0, y1 = M_TOP, H - M_BOTTOM

    out.append('<g class="grid">')
    for t in ticks_y:
        y = sy(t)
        out.append(f'<line x1="{fmt(x0)}" y1="{fmt(y)}" x2="{fmt(x1)}" y2="{fmt(y)}"/>')
    out.append("</g>")

    out.append('<g class="axis">')
    out.append(f'<line x1="{fmt(x0)}" y1="{fmt(y1)}" x2="{fmt(x1)}" y2="{fmt(y1)}"/>')
    for t in ticks_x:
        x = sx(t)
        out.append(
            f'<text class="tick" x="{fmt(x)}" y="{fmt(y1 + 26)}" text-anchor="middle">'
            f"{num(t)}</text>"
        )
    for t in ticks_y:
        y = sy(t)
        out.append(
            f'<text class="tick" x="{fmt(x0 - 16)}" y="{fmt(y)}" text-anchor="end" '
            f'dominant-baseline="middle">{num(t)}</text>'
        )
    out.append(
        f'<text class="axis-title" x="{fmt((x0 + x1) / 2)}" y="{fmt(H - 22)}" '
        f'text-anchor="middle">{esc(x_label)}</text>'
    )
    if y_label:
        out.append(
            f'<text class="axis-title" x="26" y="{fmt((y0 + y1) / 2)}" '
            f'text-anchor="middle" transform="rotate(-90 26 {fmt((y0 + y1) / 2)})">'
            f"{esc(y_label)}</text>"
        )
    out.append("</g>")
    return out


def build_scatter(cfg: dict, points: list[dict]) -> list[str]:
    x0, x1 = M_LEFT, W - M_RIGHT
    y0, y1 = M_TOP, H - M_BOTTOM

    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    marker = cfg.get("x_marker") or {}
    if marker.get("value") is not None:
        xs.append(float(marker["value"]))

    xlo, xhi = min(xs), max(xs)
    ylo, yhi = min(ys), max(ys)
    xpad = (xhi - xlo) * PAD_FRAC or max(abs(xhi) * 0.1, 1)
    ypad = (yhi - ylo) * PAD_FRAC or max(abs(yhi) * 0.1, 1)
    xlo, xhi = max(0.0, xlo - xpad), xhi + xpad
    ylo, yhi = ylo - ypad, yhi + ypad

    def sx(v: float) -> float:
        return x0 + (x1 - x0) * (v - xlo) / (xhi - xlo)

    def sy(v: float) -> float:
        return y1 - (y1 - y0) * (v - ylo) / (yhi - ylo)

    out = axis_group(
        nice_ticks(xlo, xhi),
        nice_ticks(ylo, yhi),
        sx,
        sy,
        cfg.get("x_label", ""),
        cfg.get("y_label", ""),
    )

    # Reference line, drawn under the data. This is the "what counts as good"
    # line (30 FPS, a target, a baseline), which is what turns a cloud of dots
    # into a pass or fail read.
    if marker.get("value") is not None:
        mx = sx(float(marker["value"]))
        out.append('<g class="marker">')
        out.append(f'<line x1="{fmt(mx)}" y1="{fmt(y0)}" x2="{fmt(mx)}" y2="{fmt(y1)}"/>')
        if marker.get("label"):
            out.append(
                f'<text x="{fmt(mx - 10)}" y="{fmt(y0 + 4)}" text-anchor="end">'
                f'{esc(marker["label"])}</text>'
            )
        out.append("</g>")

    if cfg.get("frontier", True) and len(points) > 2:
        front = pareto(points)
        if len(front) > 1:
            d = " ".join(f"{fmt(sx(p['x']))} {fmt(sy(p['y']))}" for p in front)
            out.append(f'<polyline class="frontier" points="{d}"/>')

    out.append('<g class="dots">')
    for p in points:
        cx, cy = sx(p["x"]), sy(p["y"])
        hi = "is-hi" if p.get("highlight") else ""
        r = DOT_R_HI if p.get("highlight") else DOT_R
        # Labels sit to the right of the dot, and flip to the left only when the
        # dot is far enough right that the label would run off the plot.
        flip = cx > x1 - 40
        tx = cx - LABEL_DX - r if flip else cx + LABEL_DX + r
        anchor = "end" if flip else "start"
        detail = f" · {p['note']}" if p.get("note") else ""
        out.append(
            f'<g class="dot {hi}">'
            f'<circle cx="{fmt(cx)}" cy="{fmt(cy)}" r="{r}"/>'
            f'<text class="dot-label" x="{fmt(tx)}" y="{fmt(cy - 5)}" '
            f'text-anchor="{anchor}">{esc(p["label"])}</text>'
            f'<text class="dot-value" x="{fmt(tx)}" y="{fmt(cy + 14)}" '
            f'text-anchor="{anchor}">{num(p["x"])} · {num(p["y"])}</text>'
            f'<title>{esc(p["label"])}: {num(p["x"])} {esc(cfg.get("x_label", ""))}, '
            f'{num(p["y"])} {esc(cfg.get("y_label", ""))}{esc(detail)}</title>'
            f"</g>"
        )
    out.append("</g>")
    return out


def build_bars(cfg: dict, points: list[dict]) -> list[str]:
    ranked = sorted(points, key=lambda p: -p["x"])

    # Bars carry their name in the left gutter rather than on the mark, so the
    # gutter has to be measured, not fixed: "Jetson Orin Nano" needs more than
    # twice the room "M1" does, and the scatter's M_LEFT silently truncated it.
    # 0.56em per character is a deliberate overestimate for the 16px stack; the
    # cap keeps one runaway label from squeezing the plot into nothing.
    longest = max((len(str(p["label"])) for p in ranked), default=0)
    x0 = min(max(M_LEFT, round(longest * 0.56 * BAR_LABEL_PX) + 26), BAR_GUTTER_MAX)
    # The right side only has to hold a value, never a label.
    x1 = W - BAR_VALUE_ROOM

    hi = max(p["x"] for p in ranked)
    xhi = hi * (1 + PAD_FRAC)

    def sx(v: float) -> float:
        # Bars are a magnitude read, so the scale is pinned to zero. No padding
        # at the low end, ever.
        return x0 + (x1 - x0) * v / xhi

    out = []
    ticks = nice_ticks(0, xhi)
    y_axis = M_TOP + len(ranked) * (BAR_H + BAR_GAP)
    out.append('<g class="grid vertical">')
    for t in ticks:
        x = sx(t)
        out.append(f'<line x1="{fmt(x)}" y1="{fmt(M_TOP)}" x2="{fmt(x)}" y2="{fmt(y_axis)}"/>')
    out.append("</g>")

    out.append('<g class="axis">')
    out.append(f'<line x1="{fmt(x0)}" y1="{fmt(y_axis)}" x2="{fmt(x1)}" y2="{fmt(y_axis)}"/>')
    for t in ticks:
        out.append(
            f'<text class="tick" x="{fmt(sx(t))}" y="{fmt(y_axis + 26)}" '
            f'text-anchor="middle">{num(t)}</text>'
        )
    out.append(
        f'<text class="axis-title" x="{fmt((x0 + x1) / 2)}" y="{fmt(y_axis + 58)}" '
        f'text-anchor="middle">{esc(cfg.get("x_label", ""))}</text>'
    )
    out.append("</g>")

    out.append('<g class="bars">')
    for i, p in enumerate(ranked):
        y = M_TOP + i * (BAR_H + BAR_GAP)
        w = max(sx(p["x"]) - x0, 1)
        cls = "is-hi" if p.get("highlight") else ""
        detail = f" · {p['note']}" if p.get("note") else ""
        out.append(
            f'<g class="bar {cls}">'
            f'<rect x="{fmt(x0)}" y="{fmt(y)}" width="{fmt(w)}" height="{BAR_H}" rx="4"/>'
            f'<text class="bar-label" x="{fmt(x0 - 16)}" y="{fmt(y + BAR_H / 2)}" '
            f'text-anchor="end" dominant-baseline="middle">{esc(p["label"])}</text>'
            f'<text class="bar-value" x="{fmt(x0 + w + 14)}" y="{fmt(y + BAR_H / 2)}" '
            f'dominant-baseline="middle">{num(p["x"])}</text>'
            f'<title>{esc(p["label"])}: {num(p["x"])} '
            f'{esc(cfg.get("x_label", ""))}{esc(detail)}</title>'
            f"</g>"
        )
    out.append("</g>")
    return out


def build() -> tuple[str, str, int]:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    cfg = profile.get("results") or {}
    points = [p for p in cfg.get("points", []) if p.get("x") is not None]
    if not points:
        raise SystemExit("profile.json needs a results.points list with at least one x")

    for p in points:
        p["x"] = float(p["x"])
        if p.get("y") is not None:
            p["y"] = float(p["y"])

    # The mark is a property of the data, not a setting. Every point needs a y
    # for the scatter to mean anything, so a single missing y drops the whole
    # chart to bars rather than plotting some points and hiding others.
    mode = "scatter" if all(p.get("y") is not None for p in points) else "bars"
    body = build_scatter(cfg, points) if mode == "scatter" else build_bars(cfg, points)

    height = H if mode == "scatter" else M_TOP + len(points) * (BAR_H + BAR_GAP) + 84
    out = [
        f'<svg class="results-chart" xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W} {fmt(height)}" role="img" '
        f'aria-label="{esc(cfg.get("title") or "Measured results")}">'
    ]
    out.extend(body)
    out.append("</svg>")
    return "\n".join(out), mode, len(points)


def main() -> None:
    svg, mode, n = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(svg, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(ROOT)}  mode={mode}  points={n}")


if __name__ == "__main__":
    main()
