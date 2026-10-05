#!/usr/bin/env python3
"""Render the Career Arc: a radial chord diagram of engagements against skills.

Reads the ``career`` and ``skills`` blocks of ``profile.json`` and writes a self
contained SVG to ``static/career_arc.svg``. The page inlines that file rather
than pointing an <img> at it, so the diagram picks up the --c1..--c6 theme
tokens off :root and repaints itself when the theme toggle flips.

Layout is a two sector chord diagram on a single ring:

* the upper sector is time. Angle maps linearly to calendar month across the
  whole career span, so a segment's arc length is that engagement's duration.
  Engagements that overlap in time are pushed onto concentric lanes by interval
  partitioning, which is what makes 2025 read as "four things at once". A ruler
  of year ticks sits inside the lanes.
* the lower sector is skills, ordered by discipline group so a skill carries the
  same color it has everywhere else on the page.
* the upper sector is colored by kind instead: hue is education, work, or
  project, and shade darkens toward the present within each kind. Reusing the
  discipline tints up there made the same color mean two different things in one
  figure.
* the chords are the join, bundled through the middle. One per (engagement,
  skill) pair, fanned evenly along the engagement's own segment instead of
  leaving from a single point.

Both sectors live on one circle so that every label radiates outward into empty
space and no chord ever has to cross a label. That constraint is the whole
reason for the two sector shape: with skills on a second, inner ring the chords
would run straight through the skill labels.

The join is filtered. An engagement only draws a chord for a skill that also
appears in profile.json's ``skills`` map, so entries like "Streamlit" or "Jetson
Nano" are carried in the career data and dropped here.

Engagement names are drawn, one block per engagement outside the ring, but the
stylesheet hides all of them until the scroll walk lights one. Shown together
they would pile up over the last stretch of the time ring, where four 2026
engagements share a few degrees. The walk's steps beside the figure carry the
same names as body text, which is the no JS reading order.

Every segment, chord and name block carries ``data-eng``, its engagement's index
in profile.json order, and every skill dot and label carries the space separated
list of engagement indices that reached it. Those are the handles the walk in
app.js toggles against. Index in **file order** is the contract: the template
numbers its steps with the same loop, so reordering the career block reorders
both together, exactly as the shade rule in assign_kinds() does. The block is
kept in start date order, because the walk steps through it in that order and
a step that jumps backwards on the time ring reads as an error.

Run it from anywhere:

    python3 tools/build_career_arc.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

# Anchored to the file, never to the working directory, so the script produces
# the same output whether it is invoked from the repo root, from tools/, or by
# an editor with some unrelated cwd.
ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "profile.json"
OUT_PATH = ROOT / "static" / "career_arc.svg"

# ---------------------------------------------------------------------------
# Geometry. Every angle in this module is degrees; pt() is the only place they
# become radians, so there is exactly one conversion to get wrong.
# ---------------------------------------------------------------------------
SIZE = 1160
CX = CY = SIZE / 2

# The ring sits well inside the box so the skill labels have room to grow. The
# longest label leaves the ring at roughly 0 degrees, dead horizontal, where it
# has the least space, so SIZE/2 - R_SKILL_LABEL is the real constraint on how
# large .skill-label can be set in the stylesheet.
#
# .skill-label went from 16px to 20px on 2026-08-27, so the room a label needs
# radially grew with it: the longest ("Model Experimentation") measures ~188
# units at 20px, and SIZE/2 - R_SKILL_LABEL = 200 is what it has. That is a thin
# margin and it is the ceiling on R_SKILL_LABEL, not a free number.
#
# The ruler radii move with the ring and have to: the ruler sits inside the
# innermost engagement lane, so changing the ring without changing the ruler puts
# the year ticks on top of the lanes.
R_RING = 368  # the shared circle both sectors sit on
LANE_STEP = 17  # each overlapping engagement steps one lane inward
R_SKILL_LABEL = 380  # skill text starts here and radiates outward
R_RULE_TICK_OUT = 270  # year ruler, inside the innermost lane
R_RULE_TICK_IN = 257
R_RULE_LABEL = 240
DOT_R = 4.0  # skill dot radius; grown with the type so it stays visible

# Sector bounds, clockwise in SVG's y down space. 90 is the bottom of the
# circle and 270 is the top, so skills fill the lower half and time reads left
# to right across the upper half.
#
# The split between them is the lever that let the ring keep all 47 skills at
# 20px type, and it is worth understanding before either number is touched.
# Tangential room per label is R_SKILL_LABEL * (span - gaps) / n, and a 20px line
# box needs ~23 units of it. At the original 200 degrees, 47 labels got 22.7
# units and touched, which is why cutting the list looked unavoidable. At 220 they
# get 27.5, a 20% margin, matching what 38 labels had at 200.
#
# The 20 degrees came out of the time sector, which is the half that had room to
# give: it now spans 120 degrees for a 46 month career, so a two month engagement
# still draws 5.2 degrees of arc. Going further was measured and does not pay.
# At 240 the skill sector reaches far enough round the upper left that the hub
# caption block collides with the "JavaScript" label.
SKILL_A0, SKILL_A1 = 0.0, 220.0
TIME_A0, TIME_A1 = 230.0, 350.0
GROUP_GAP = 5.0  # degrees of blank ring between two discipline groups

RIM_W = 11  # engagement segment stroke width
BUNDLE = 0.30  # chord control point radius, as a fraction of the chord midpoint

# Engagement names and the opening callouts. These px values must match
# .sl-name, .sl-yrs and .g-pill text in styles.css, since the builder sizes the
# clamp and the pills from them.
SEG_LABEL_GAP = 40  # name block's anchor, this far outside the ring
SEG_NAME_PX = 30
SEG_YEARS_PX = 22
GUIDE_PX = 28
EDGE = 24  # nearest a name block may come to the edge of the box

TINTS = 6  # discipline groups on the skill ring, .t1 .. .t6 in the stylesheet

# A cap on how many skills the ring may carry. None draws every skill that
# survives the join, and None is correct here: nothing is currently cut.
#
# Kept, unset, because the constraint it answers is real and will come back. The
# figure's height and its legibility are the same number: it is square and sized
# to its column, so its width sets both how tall the section is and how large the
# labels render. Legible at 11px needs a rendered width of at least
# 11 * SIZE / font-size; fitting a 900px viewport alongside the title, note and
# legend needs at most ~670px. At the original 16px type there was no width that
# satisfied both, which is why scaling the figure never helped.
#
# Raising the type to 20px fixed that, and the angular room it needed came from
# widening the skill sector rather than from cutting labels: see SKILL_A1 below.
# Cutting to 38 was tried first and was not necessary. If the skill list grows
# far enough that even 220 degrees cannot hold it, set this to a number and
# select_skills() will apportion the cut across disciplines.
TOP_N: int | None = None

# Engagements are colored by kind, not by position in the list. Six rotating
# tints made two adjacent segments look unrelated and collided with the .t1..t6
# the skill ring already uses for disciplines, so the same color meant
# "discipline" at the bottom of the figure and "which engagement" at the top.
# Hue now carries kind and shade carries recency, which is the one thing the
# upper sector is actually about.
KIND_CLASS = {"education": "k-edu", "work": "k-work", "project": "k-proj"}
KIND_FALLBACK = "project"
KIND_ORDER = ["education", "work", "project"]
KIND_LABEL = {"education": "Education", "work": "Work", "project": "Projects"}

# Oldest engagement of a kind to the newest, as a color-mix percentage of the
# kind's tint against --paper. The floor stays well clear of 0 so an early
# segment reads as a settled version of its hue rather than as background.
SHADE_MIN, SHADE_MAX = 62.0, 100.0


def pt(r: float, deg: float) -> tuple[float, float]:
    """Polar to cartesian, in SVG's y down coordinate space."""
    rad = math.radians(deg)
    return CX + r * math.cos(rad), CY + r * math.sin(rad)


def text_w(text: str, px: float) -> float:
    """Rough rendered width of `text` at `px`, in viewBox units.

    An estimate, not a measurement: the build has no font metrics. 0.56 em per
    character is generous for the sans stack, so a clamp built on it errs inward.
    """
    return len(text) * px * 0.56


def fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def arc_path(r: float, a0: float, a1: float) -> str:
    """An SVG arc command sweeping clockwise from a0 to a1 at radius r."""
    x0, y0 = pt(r, a0)
    x1, y1 = pt(r, a1)
    large = 1 if abs(a1 - a0) > 180 else 0
    return f"M {fmt(x0)} {fmt(y0)} A {fmt(r)} {fmt(r)} 0 {large} 1 {fmt(x1)} {fmt(y1)}"


def months(ym: str) -> int:
    """Turn "2024-08" into an absolute month index."""
    year, month = ym.split("-")
    return int(year) * 12 + int(month) - 1


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def assign_lanes(entries: list[dict]) -> None:
    """Interval partitioning: give each entry the outermost free lane.

    Entries are walked in start order and dropped into the first lane whose last
    occupant has already ended, so two engagements share a lane only when they
    do not overlap in time. That is what the concentric rings encode.
    """
    lane_ends: list[int] = []
    for e in sorted(entries, key=lambda x: x["_t0"]):
        for i, end in enumerate(lane_ends):
            if e["_t0"] >= end:
                e["_lane"] = i
                lane_ends[i] = e["_t1"]
                break
        else:
            e["_lane"] = len(lane_ends)
            lane_ends.append(e["_t1"])


def assign_kinds(entries: list[dict]) -> None:
    """Give each entry its kind class and a recency shade within that kind.

    The ordinal is position among the entries of the same kind **in profile.json
    order**, not in date order. That is deliberate: the walk's steps iterate
    ``profile.career`` in file order and recompute the identical shade in Jinja,
    so a step and its segment only stay the same color while both count the same
    way. The file is kept in date order anyway, so the two agree today. Reordering profile.json reorders both together; sorting here would
    silently split them.
    """
    seen: dict[str, int] = {}
    for e in entries:
        kind = e.get("kind") if e.get("kind") in KIND_CLASS else KIND_FALLBACK
        e["_kind"] = kind
        e["_ord"] = seen.get(kind, 0)
        seen[kind] = e["_ord"] + 1
    for e in entries:
        n = seen[e["_kind"]]
        span = (SHADE_MAX - SHADE_MIN) * e["_ord"] / (n - 1) if n > 1 else SHADE_MAX - SHADE_MIN
        e["_shade"] = SHADE_MIN + span


def apportion(sizes: list[int], total: int) -> list[int]:
    """Split `total` seats across groups in proportion to `sizes`.

    Largest remainder, with a floor of one seat for any group that has anything
    to show and a ceiling of what the group actually holds. Plain proportional
    rounding would let a small discipline round to zero, which is the one outcome
    the caller cannot accept.
    """
    live = sum(sizes)
    if live <= total:
        return list(sizes)

    exact = [total * s / live for s in sizes]
    seats = [min(s, max(1 if s else 0, int(e))) for s, e in zip(sizes, exact)]

    while sum(seats) < total:
        room = [i for i in range(len(sizes)) if seats[i] < sizes[i]]
        if not room:
            break
        seats[max(room, key=lambda i: exact[i] - seats[i])] += 1
    while sum(seats) > total:
        room = [i for i in range(len(sizes)) if seats[i] > 1]
        if not room:
            break
        seats[min(room, key=lambda i: exact[i] - seats[i])] -= 1
    return seats


def select_skills(
    groups: list[tuple[str, list[str]]],
    career: list[dict],
    counts: dict[str, int],
    order: dict[str, int],
) -> set[str]:
    """Cap the ring at TOP_N skills, cutting each discipline in proportion.

    Ranking *within* a discipline is chord count, which is the figure's own
    measure of importance: a skill three engagements ran to carries three chords
    and is load bearing in the drawing, where a skill one engagement touched is a
    single line into a label. Ties break on position in profile.json, so the
    ordering is deterministic across runs and the author can promote a skill by
    moving it up its group rather than by editing this file.

    Ranking **across** disciplines was tried first and was wrong, which is worth
    keeping written down because it is the obvious implementation. Chord count
    correlates with how recent an engagement is, so a global cut takes almost
    everything from the oldest work: at TOP_N=38 it left Web & Mobile at 1 of 6
    and Languages at 2 of 5, dropping C++, Java and Swift while every ML skill
    survived. That is not a summary of the career, it is a picture of the last
    two years, and on a page whose job is to represent all of it that reads as a
    gap rather than as an edit. Apportioning by discipline keeps the same shape
    the full ring had: at the same TOP_N the split is 5/7/5/12/5/4.

    It also keeps the composition. The lower sector gives each discipline a share
    of its arc proportional to how many skills it has, so cutting proportionally
    leaves every sector the width it had, where a global cut visibly collapsed
    two of them.

    One guard survives on top of the quotas: **every engagement keeps at least
    its strongest skill**, even if that overshoots TOP_N. An engagement with no
    chords still draws a segment and still has a legend chip, and that chip would
    filter the figure down to nothing, which reads as a broken control rather
    than as an omission.
    """
    candidates = set(counts)
    if TOP_N is None or len(candidates) <= TOP_N:
        return candidates

    def rank(skill: str) -> tuple[int, int]:
        return (-counts.get(skill, 0), order.get(skill, 1 << 30))

    live = [sorted([s for s in members if s in candidates], key=rank) for _, members in groups]
    seats = apportion([len(m) for m in live], TOP_N)

    keep: set[str] = set()
    for members, n in zip(live, seats):
        keep.update(members[:n])

    for e in career:
        if e["_skills"] and not any(s in keep for s in e["_skills"]):
            keep.add(min(e["_skills"], key=rank))
    return keep


def place_skills(groups: list[tuple[str, list[str]]], used: set[str]) -> dict:
    """Position every surviving skill in the lower sector, grouped by discipline.

    Each group gets a share of the sector proportional to how many of its skills
    survived the join, minus a fixed gap so the disciplines stay visually
    separate. A group with nothing left is skipped rather than left as a hole.
    """
    live = [(name, [s for s in members if s in used]) for name, members in groups]
    live = [(name, members) for name, members in live if members]
    total = sum(len(m) for _, m in live)
    budget = (SKILL_A1 - SKILL_A0) - GROUP_GAP * (len(live) - 1)
    index = {name: i for i, (name, _) in enumerate(groups)}

    placed: dict[str, dict] = {}
    cursor = SKILL_A0
    for name, members in live:
        span = budget * len(members) / total
        step = span / len(members)
        for i, skill in enumerate(members):
            # Half a step of padding at each end centres the group in its share.
            placed[skill] = {"angle": cursor + step * (i + 0.5), "group": index[name]}
        cursor += span + GROUP_GAP
    return placed


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------
def build() -> tuple[str, int, int]:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    career = profile.get("career") or []
    groups = list(profile.get("skills", {}).items())
    if not career or not groups:
        raise SystemExit("profile.json needs both a career list and a skills map")

    known = {s for _, members in groups for s in members}
    # Flattened profile.json position, the deterministic tiebreak in select_skills.
    order = {s: i for i, s in enumerate(s for _, members in groups for s in members)}

    for i, e in enumerate(career):
        e["_i"] = i  # file order, the handle the walk addresses
        e["_t0"] = months(e["start"])
        e["_t1"] = months(e["end"])
        # The join. Entry order is preserved so the fan of chords leaving a
        # segment stays stable between runs.
        e["_skills"] = [s for s in e["skills"] if s in known]

    t0 = min(e["_t0"] for e in career)
    t1 = max(e["_t1"] for e in career)
    assign_lanes(career)
    assign_kinds(career)

    # How many engagements reached each skill, counted before the cap so the
    # ranking sees the full join and so the caption can say what was left out.
    counts: dict[str, int] = {}
    for e in career:
        for s in e["_skills"]:
            counts[s] = counts.get(s, 0) + 1
    joined = len(counts)

    # Drop the cut skills from the join as well as from the ring. A chord whose
    # destination was never placed would raise a KeyError below, and a segment
    # fanning chords to a skill with no label would be drawing a line to nowhere.
    survivors = select_skills(groups, career, counts, order)
    for e in career:
        e["_skills"] = [s for s in e["_skills"] if s in survivors]

    used = {s for e in career for s in e["_skills"]}
    skills = place_skills(groups, used)

    # Which engagements reached each skill. The filter dims a skill once every
    # engagement that used it is switched off, so the ring stops advertising
    # skills that no longer have a chord running to them.
    skill_engs: dict[str, list[int]] = {}
    for e in career:
        for s in e["_skills"]:
            skill_engs.setdefault(s, []).append(e["_i"])

    def ang(t: int) -> float:
        return TIME_A0 + (TIME_A1 - TIME_A0) * (t - t0) / (t1 - t0)

    out: list[str] = []
    add = out.append

    add(
        f'<svg class="career-arc" xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {SIZE} {SIZE}" role="img" '
        f'aria-label="Radial diagram linking each engagement to the skills it used">'
    )

    # Year ruler -------------------------------------------------------------
    add('<g class="rule">')
    add(f'<path class="rule-arc" d="{arc_path(R_RULE_TICK_OUT, TIME_A0, TIME_A1)}"/>')
    for year in range(t0 // 12, t1 // 12 + 1):
        t = year * 12
        if not t0 <= t <= t1:
            continue
        a = ang(t)
        x0, y0 = pt(R_RULE_TICK_IN, a)
        x1, y1 = pt(R_RULE_TICK_OUT, a)
        lx, ly = pt(R_RULE_LABEL, a)
        add(f'<line x1="{fmt(x0)}" y1="{fmt(y0)}" x2="{fmt(x1)}" y2="{fmt(y1)}"/>')
        add(
            f'<text x="{fmt(lx)}" y="{fmt(ly)}" text-anchor="middle" '
            f'dominant-baseline="middle">{year}</text>'
        )
    add("</g>")

    # Chords -----------------------------------------------------------------
    # Emitted before the ring so the segments and dots sit on top of their fan.
    add('<g class="arcs">')
    arcs = 0
    for e in career:
        a0, a1 = ang(e["_t0"]), ang(e["_t1"])
        r_rim = R_RING - LANE_STEP * e["_lane"]
        n = len(e["_skills"])
        for i, skill in enumerate(e["_skills"]):
            # Fan the origins across the segment rather than stacking them on
            # the midpoint. A single skill engagement leaves from the middle.
            f = (i + 0.5) / n if n > 1 else 0.5
            sx, sy = pt(r_rim - RIM_W / 2 - 1, a0 + (a1 - a0) * f)
            ex, ey = pt(R_RING - RIM_W / 2 - 1, skills[skill]["angle"])

            # Pull the control point toward the centre so the chords bundle
            # through the middle instead of filling the disc with straight lines.
            mx, my = (sx + ex) / 2 - CX, (sy + ey) / 2 - CY
            mr = math.hypot(mx, my) or 1.0
            qx, qy = CX + mx * BUNDLE, CY + my * BUNDLE

            # --i is the draw in stagger index, read by the stylesheet. Emitting
            # it here keeps the animation ordering tied to the emission order
            # rather than to a second pass in JavaScript.
            add(
                f'<path class="arc {KIND_CLASS[e["_kind"]]}" data-eng="{e["_i"]}" '
                f'style="--i:{arcs};--shade:{fmt(e["_shade"])}%" '
                f'd="M {fmt(sx)} {fmt(sy)} Q {fmt(qx)} {fmt(qy)} {fmt(ex)} {fmt(ey)}">'
                f"<title>{esc(e['label'])} → {esc(skill)}</title></path>"
            )
            arcs += 1
    add("</g>")

    # Engagement lanes -------------------------------------------------------
    add('<g class="rim">')
    for e in career:
        d = arc_path(R_RING - LANE_STEP * e["_lane"], ang(e["_t0"]), ang(e["_t1"]))
        # `detail` is optional, so read it with a default and drop the separator
        # when it is absent. scripts/setup_profile.py collects it per engagement
        # but its résumé import does not, so a fresh fork that accepted the
        # import's proposals reached here with the key missing. That raised
        # KeyError, run_builders reported "failed." and carried on, and the fork
        # kept the previous owner's figure: the exact silent staleness that
        # rebuilding the figures here exists to prevent.
        detail = e.get("detail", "")
        title = f'{esc(e["label"])} · {esc(detail)}' if detail else esc(e["label"])
        add(
            f'<path class="seg {KIND_CLASS[e["_kind"]]}" data-eng="{e["_i"]}" '
            f'style="--shade:{fmt(e["_shade"])}%" d="{d}">'
            f'<title>{title}</title></path>'
        )
    add("</g>")

    # Skill ring -------------------------------------------------------------
    # "skill-ring", not "skills": .skills is already a page level section class
    # in styles.css and the diagram is inlined into that same document.
    add('<g class="skill-ring">')
    for skill, node in skills.items():
        a = node["angle"]
        tint = node["group"] % TINTS + 1
        x, y = pt(R_RING, a)
        lx, ly = pt(R_SKILL_LABEL, a)
        owners = " ".join(str(i) for i in skill_engs.get(skill, []))
        add(
            f'<circle class="dot t{tint}" data-eng="{owners}" '
            f'cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(DOT_R)}"/>'
        )
        # Flip the labels on the left half so no text ever reads upside down.
        flip = 90 < a % 360 < 270
        rot = a + 180 if flip else a
        add(
            f'<text class="skill-label t{tint}" data-eng="{owners}" '
            f'x="{fmt(lx)}" y="{fmt(ly)}" '
            f'text-anchor="{"end" if flip else "start"}" dominant-baseline="middle" '
            f'transform="rotate({fmt(rot)} {fmt(lx)} {fmt(ly)})">{esc(skill)}</text>'
        )
    add("</g>")

    # Engagement names -------------------------------------------------------
    # One name and year range per engagement, outside the ring at its band's
    # midpoint. All of them are emitted and the walk shows one at a time, which
    # is why they can share the same patch of sky without colliding. The name has
    # to be on the figure itself: the step beside it is 400px away, and a lit
    # band with no name on it was the main reason the figure did not read.
    #
    # Width is estimated from character count rather than measured, so the
    # block can be clamped inside the box at build time. Without the clamp the
    # last engagement on the time ring runs off the right edge of the card.
    add('<g class="seg-labels">')
    for e in career:
        a = (ang(e["_t0"]) + ang(e["_t1"])) / 2
        x, y = pt(R_RING + SEG_LABEL_GAP, a)
        y0, y1 = e["start"][:4], e["end"][:4]
        yrs = y0 if y0 == y1 else f"{y0}–{y1}"
        w = max(text_w(e["label"], SEG_NAME_PX), text_w(yrs, SEG_YEARS_PX))
        c = math.cos(math.radians(a))
        # Centred near the top of the ring, hung off whichever side faces out
        # elsewhere, so the text always grows away from the ring.
        if abs(c) < 0.35:
            left = x - w / 2
        elif c > 0:
            left = x
        else:
            left = x - w
        left = min(max(left, EDGE), SIZE - EDGE - w)
        add(
            f'<g class="seg-label {KIND_CLASS[e["_kind"]]}" data-eng="{e["_i"]}" '
            f'style="--shade:{fmt(e["_shade"])}%">'
            f'<text class="sl-name" x="{fmt(left)}" y="{fmt(y - 30)}">{esc(e["label"])}</text>'
            f'<text class="sl-yrs" x="{fmt(left)}" y="{fmt(y - 4)}">{yrs}</text></g>'
        )
    add("</g>")

    # How to read it ---------------------------------------------------------
    # Three callouts for the walk's opening step, which isolates the first
    # engagement. Pills rather than bare text: they sit on top of the chords,
    # and plain or haloed type over a fan of lines was the first thing every
    # reader said they could not read. Positions are radii chosen to clear the
    # year labels (R_RULE_LABEL) and the skill labels at the bottom.
    def pill(cx: float, cy: float, text: str) -> None:
        w = text_w(text, GUIDE_PX) + 36
        h = GUIDE_PX + 20
        add(
            f'<g class="g-pill"><rect x="{fmt(cx - w / 2)}" y="{fmt(cy - h / 2)}" '
            f'width="{fmt(w)}" height="{fmt(h)}" rx="{fmt(h / 2)}"/>'
            f'<text x="{fmt(cx)}" y="{fmt(cy)}" text-anchor="middle" '
            f'dominant-baseline="central">{esc(text)}</text></g>'
        )

    add('<g class="guide">')
    pill(*pt(150, 270), "Time runs left to right →")
    pill(CX, CY + 40, "Each line is one skill used on that band")
    pill(*pt(250, 90), "↓ Skills sit around the bottom")
    add("</g>")

    # Summary and key --------------------------------------------------------
    # Parked in the corner the two sectors leave empty rather than in the middle,
    # where it would sit under the bundle. The chord count that used to headline
    # this block was a fact about the drawing, not about the career, so the two
    # lines that say something are all that is left.
    add('<g class="hub">')
    # "38 of 47" rather than a bare "38" whenever the cap bit. The figure is a
    # selection now, and a caption that quietly reported the drawn count as the
    # whole would be understating the career to make the drawing look complete.
    drawn = len(skills)
    head = (
        f"{drawn} of {joined} skills" if drawn < joined else f"{drawn} skills"
    )
    add(f'<text class="hub-k" x="48" y="80">{head} across {len(career)} engagements</text>')
    add(
        f'<text class="hub-k" x="48" y="106">{(t1 - t0) / 12:.1f} years, '
        f"{t0 // 12} to {t1 // 12}</text>"
    )

    # Key for the three segment hues. Without it the reader has to infer from
    # the legend chips below the figure that color means kind and not identity.
    y = 150
    for kind in KIND_ORDER:
        if not any(e["_kind"] == kind for e in career):
            continue
        cls = KIND_CLASS[kind]
        add(f'<line class="key-swatch {cls}" x1="48" y1="{y - 5}" x2="76" y2="{y - 5}"/>')
        add(f'<text class="hub-k" x="86" y="{y}">{KIND_LABEL[kind]}</text>')
        y += 24
    add("</g>")

    add("</svg>")
    return "\n".join(out), arcs, len(skills), joined


def main() -> None:
    svg, arcs, n_skills, joined = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # The encoding is pinned rather than inherited from the locale: the diagram
    # carries U+2192 and U+00B7, and a C locale would otherwise fail the write.
    OUT_PATH.write_text(svg, encoding="utf-8")
    cut = joined - n_skills
    note = f"  (cut {cut} of {joined}, TOP_N={TOP_N})" if cut else ""
    print(f"wrote {OUT_PATH.relative_to(ROOT)}  skills={n_skills}  arcs={arcs}{note}")


if __name__ == "__main__":
    main()
