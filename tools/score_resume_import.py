#!/usr/bin/env python3
"""Score the résumé import against hand-written answer keys.

    .venv/bin/python tools/score_resume_import.py ~/fakes/*.pdf

Each `resume_01.pdf` is scored against `resume_01.key.json` beside it. The key
is ground truth, every value copied verbatim out of the résumé:

    name, role, email, location, tagline   string, or null if absent
    links          [{label, url}]
    skills         {group: [skill, ...]}
    engagements    [{label, kind: work|project|education,
                     start: YYYY-MM|YYYY, end: YYYY-MM|YYYY|present,
                     skills: [...], bullets: [...]}]
    must_not_extract   [phone, street address, ...]; must not reach knowledge/
    layout_traits      free text, used to group problems by cause

inspect_resume_import.py finds output that *looks* wrong. This finds output
that *is* wrong or *missing*, which inspect cannot see: a résumé whose third job
was never found smells of nothing.

Every field is scored on what setup would actually write, through
`read_resume`, and the leak check runs over the rendered `knowledge/` files,
because a phone number in a bullet only matters if it reaches the corpus.

It writes nothing. Exit status is 1 when a `must_not_extract` value leaked.
"""

import argparse
import json
import re
import sys
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import setup_profile as sp  # noqa: E402

# A label or bullet counts as found above this similarity. Not 1.0: the import
# trims labels at 92 chars and scrubs contact details out of bullets, both on
# purpose, and neither should read as a miss.
LABEL_MATCH = 0.6
BULLET_MATCH = 0.85

SCALARS = ("name", "role", "email", "location", "tagline")


def norm(text: str | None) -> str:
    """Compare what a reader would call the same text as the same text."""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[‐-―−]", "-", text)
    text = re.sub(r"[‘’]", "'", re.sub(r"[“”]", '"', text))
    return " ".join(text.split()).strip(" .").casefold()


def norm_url(url: str) -> str:
    url = re.sub(r"^https?://(www\.)?", "", url.strip().casefold())
    return url.rstrip("/")


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def pr(found: set, truth: set) -> tuple[int, int, int]:
    """(hits, len(found), len(truth)), summed across résumés before dividing."""
    return len(found & truth), len(found), len(truth)


def date_ok(found: str, truth: str) -> bool:
    """A key date is YYYY-MM, YYYY, or 'present'. The import writes 'present' as
    the current month (prompt_month rejects the word), and a year-only date gets
    a month the résumé never stated, so only the year is compared."""
    truth = (truth or "").strip().casefold()
    if truth in ("present", "current"):
        return found == date.today().strftime("%Y-%m")
    if re.fullmatch(r"\d{4}", truth):
        return found[:4] == truth
    return found == truth


def align(found: list[dict], truth: list[dict]) -> list[tuple[dict, dict | None]]:
    """Pair each key engagement with its best unclaimed found one, best first.

    Greedy by similarity rather than by order, because the import finds
    engagements in the order dates appear and a two-column résumé scrambles that.
    """
    pairs = sorted(((similar(f["label"], t["label"]), i, j)
                    for i, t in enumerate(truth) for j, f in enumerate(found)),
                   reverse=True)
    taken_t: dict[int, int] = {}
    taken_f: set[int] = set()
    for score, i, j in pairs:
        if score < LABEL_MATCH or i in taken_t or j in taken_f:
            continue
        taken_t[i] = j
        taken_f.add(j)
    # Then by dates alone, for what the labels could not pair. The two-column
    # failure finds every date right and every label empty; reporting that as
    # one miss plus one invention hides the half the parser got right.
    for i, t in enumerate(truth):
        for j, f in enumerate(found):
            if i in taken_t or j in taken_f:
                continue
            if date_ok(f["start"], t.get("start", "")) and date_ok(f["end"], t.get("end", "")):
                taken_t[i] = j
                taken_f.add(j)
    return [(t, found[taken_t[i]] if i in taken_t else None) for i, t in enumerate(truth)]


def bullet_hits(found: list[str], truth: list[str]) -> tuple[int, int, int]:
    """(recalled, precise, emitted, expected).

    Recall and precision get separate numerators because they differ when the
    import splits one bullet in two: neither half matches, so both miss.
    """
    recalled = sum(any(similar(f, t) >= BULLET_MATCH for f in found) for t in truth)
    precise = sum(any(similar(f, t) >= BULLET_MATCH for t in truth) for f in found)
    return recalled, precise, len(found), len(truth)


def rendered_corpus(proposal: dict) -> str:
    """What setup would write into knowledge/, from this proposal alone."""
    contact = proposal["contact"]
    career = [dict(e) for e in proposal["career"]]
    bullets = {e["label"]: e.pop("bullets") for e in career}
    profile = {**contact, "skills": proposal["skills"], "career": career}
    return sp.render_resume_md(profile, bullets) + "\n" + sp.render_about_me(profile)


def leaked(secret: str, corpus: str) -> bool:
    """A phone survives reformatting, so digits are compared as digits."""
    digits = re.sub(r"\D", "", secret)
    if len(digits) >= 7:
        return digits in re.sub(r"\D", "", corpus) or digits[-7:] in re.sub(r"\D", "", corpus)
    return norm(secret) in norm(corpus)


class Tally:
    def __init__(self) -> None:
        self.scalar: dict[str, list[int]] = {k: [0, 0] for k in SCALARS}
        self.sets: dict[str, list[int]] = {}
        self.eng: dict[str, list[int]] = {k: [0, 0] for k in ("label", "start", "end", "kind")}
        self.bullets = [0, 0, 0, 0]
        self.leaks: list[str] = []
        self.misses: dict[str, int] = {}

    def add_set(self, name: str, hits: tuple) -> None:
        row = self.sets.setdefault(name, [0] * len(hits))
        for k, v in enumerate(hits):
            row[k] += v


def score(pdf: Path, key: dict, tally: Tally, traits: dict) -> None:
    print(f"\n{'=' * 78}\n{pdf.name}   traits: {', '.join(key.get('layout_traits', [])) or '-'}\n{'=' * 78}")
    proposal = sp.read_resume(pdf)
    if proposal is None:
        print("  unreadable")
        tally.misses["unreadable"] = tally.misses.get("unreadable", 0) + 1
        return
    got = proposal["contact"]
    problems: list[str] = []

    for field in SCALARS:
        truth = key.get(field)
        if truth is None:
            ok = not got.get(field)
            want = "(none)"
        else:
            ok = norm(got.get(field)) == norm(truth)
            want = truth
            tally.scalar[field][1] += 1
            tally.scalar[field][0] += ok
        if not ok:
            problems.append(f"{field}: got {str(got.get(field))[:60]!r}, want {want[:60]!r}")

    link_hits = pr({norm_url(l["url"]) for l in got.get("links", [])},
                   {norm_url(l["url"]) for l in key.get("links", [])})
    tally.add_set("links", link_hits)
    if link_hits[0] < link_hits[2]:
        problems.append(f"links: {link_hits[0]}/{link_hits[2]} found")

    got_skills = {norm(s) for m in proposal["skills"].values() for s in m}
    key_skills = {norm(s) for m in key.get("skills", {}).values() for s in m}
    tally.add_set("skills", pr(got_skills, key_skills))
    tally.add_set("skill groups", pr({norm(g) for g in proposal["skills"]},
                                     {norm(g) for g in key.get("skills", {})}))
    missing = key_skills - got_skills
    if missing:
        problems.append(f"skills missed ({len(missing)}): {', '.join(sorted(missing)[:8])}")
    extra = got_skills - key_skills
    if extra:
        problems.append(f"skills invented ({len(extra)}): {', '.join(sorted(extra)[:8])}")

    truth_eng = key.get("engagements", [])
    pairs = align(proposal["career"], truth_eng)
    matched = [f for _, f in pairs if f]
    tally.add_set("engagements", (len(matched), len(proposal["career"]), len(truth_eng)))
    for t, f in pairs:
        if f is None:
            problems.append(f"engagement MISSING: {t['label'][:70]!r}")
            continue
        label_ok = similar(f["label"], t["label"]) >= LABEL_MATCH
        tally.eng["label"][0] += label_ok
        tally.eng["label"][1] += 1
        if not label_ok:
            problems.append(f"label: got {f['label'][:40]!r}, want {t['label'][:40]!r}")
        for part in ("start", "end"):
            ok = date_ok(f[part], t.get(part, ""))
            tally.eng[part][0] += ok
            tally.eng[part][1] += 1
            if not ok:
                problems.append(f"{t['label'][:40]!r} {part}: got {f[part]}, want {t.get(part)}")
        ok = f["kind"] == t.get("kind")
        tally.eng["kind"][0] += ok
        tally.eng["kind"][1] += 1
        if not ok:
            problems.append(f"{t['label'][:40]!r} kind: got {f['kind']}, want {t.get('kind')}")
        tally.add_set("engagement skills", pr({norm(s) for s in f["skills"]},
                                              {norm(s) for s in t.get("skills", [])}))
        b = bullet_hits(f["bullets"], t.get("bullets", []))
        tally.bullets = [x + y for x, y in zip(tally.bullets, b)]
        if b[0] < b[3]:
            problems.append(f"{t['label'][:40]!r} bullets: {b[0]}/{b[3]} recovered, {b[2]} emitted")
    for f in proposal["career"]:
        if f not in matched:
            problems.append(f"engagement INVENTED: {f['label'][:70]!r}")

    corpus = rendered_corpus(proposal)
    for secret in key.get("must_not_extract", []):
        if leaked(secret, corpus):
            problems.append(f"LEAK into knowledge/: {secret!r}")
            tally.leaks.append(f"{pdf.name}: {secret}")

    for p in problems:
        print(f"  ! {p}")
    if not problems:
        print("  perfect")
    for trait in key.get("layout_traits", []):
        row = traits.setdefault(trait, [0, 0])
        row[0] += len(problems)
        row[1] += 1


def pct(hits: int, total: int) -> str:
    return f"{hits / total:6.0%} ({hits}/{total})" if total else "     -"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdfs", nargs="+", type=Path, help="résumé PDFs, each with a .key.json beside it")
    parser.add_argument("--keys", type=Path,
                        help="read keys from this folder instead, so one set grades every producer's PDFs")
    args = parser.parse_args()

    tally = Tally()
    traits: dict[str, list[int]] = {}
    scored = 0
    for pdf in args.pdfs:
        key_path = (args.keys / pdf.stem if args.keys else pdf.with_suffix("")).with_suffix(".key.json")
        if not key_path.is_file():
            print(f"\nskipping {pdf.name}: no {key_path.name}")
            continue
        score(pdf, json.loads(key_path.read_text(encoding="utf-8")), tally, traits)
        scored += 1

    print(f"\n{'=' * 78}\nTOTALS over {scored} résumé(s)\n{'=' * 78}")
    print(f"  {'field':20} {'correct':>16}")
    for field, (hits, total) in tally.scalar.items():
        print(f"  {field:20} {pct(hits, total):>16}")
    for part, (hits, total) in tally.eng.items():
        print(f"  {'engagement ' + part:20} {pct(hits, total):>16}")
    print(f"\n  {'set':20} {'recall':>16} {'precision':>16}")
    for name, (hits, found, truth) in tally.sets.items():
        print(f"  {name:20} {pct(hits, truth):>16} {pct(hits, found):>16}")
    recalled, precise, found, truth = tally.bullets
    print(f"  {'bullets':20} {pct(recalled, truth):>16} {pct(precise, found):>16}")

    if traits:
        print(f"\n  {'layout trait':34} {'problems per résumé':>20}")
        for trait, (problems, count) in sorted(traits.items(), key=lambda kv: -kv[1][0] / kv[1][1]):
            print(f"  {trait[:34]:34} {problems / count:>14.1f}  (n={count})")

    print(f"\n  leaks: {len(tally.leaks)}")
    for leak in tally.leaks:
        print(f"  ! {leak}")
    return 1 if tally.leaks else 0


if __name__ == "__main__":
    sys.exit(main())
