#!/usr/bin/env python3
"""Show what the résumé import reads out of a PDF, and flag what looks wrong.

    .venv/bin/python tools/inspect_resume_import.py cv1.pdf cv2.pdf ...

Every bug this parser has had produced *plausible* output: a real label with the
wrong words in it, a real engagement claiming another engagement's skills, a real
location that was two place names glued together. None of them raised. So the
point of this tool is not to run the parsers, which is easy, but to say which of
their answers smell wrong, because that is the part that does not survive being
eyeballed across twenty résumés.

It writes nothing. `setup_profile.py` is still the only thing that writes a
profile, and this shares its parsers rather than restating them.

Exit status is 1 when anything was flagged, so a batch can be run in a loop.
"""

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import setup_profile as sp  # noqa: E402
import resume_import as ri  # noqa: E402


def load(path: Path) -> str | None:
    """Read a PDF through the real extractor, or a text file as-is."""
    if path.suffix.lower() == ".pdf":
        return ri.read_pdf_text(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    # Said loudly, because it invalidates most of what this tool would report.
    # pypdf extracted the résumé this parser was built against as ONE line with
    # no newlines at all, and runs of two or more spaces where the layout had a
    # column break. Four of the parser's nine fixed bugs exist only in that
    # shape. A tidy text file tests a document pypdf never produces.
    print("  ! NOT A PDF. A text file has newlines where a real extraction has")
    print("    none, and no layout gaps, so the label and bullet boundaries here")
    print("    are not the ones a real import faces. Render it to PDF first.")
    return text


def flag(warnings: list[str], condition: bool, message: str) -> None:
    if condition:
        warnings.append(message)


def check_contact(found: dict, warnings: list[str]) -> None:
    name = found.get("name", "")
    flag(warnings, not name, "no name found in the header")
    flag(warnings, bool(ri._TITLE_WORD.search(name)),
         f"name {name!r} contains a job-title word, so the run was not cut")
    flag(warnings, len(name.split()) > 4, f"name {name!r} is more than four words")
    role = found.get("role", "")
    flag(warnings, bool(role) and len(role) > 70, f"role {role!r} is too long to be a title")
    flag(warnings, not found.get("email"), "no email found")
    location = found.get("location", "")
    # The failure mode was "University of Somewhere Augusta, GA" -> "Somewhere
    # Augusta, GA": a city that is really the tail of the institution above it.
    flag(warnings, len(location.split(",")[0].split()) > 2,
         f"location {location!r} has a suspiciously long city")
    tagline = found.get("tagline", "")
    flag(warnings, bool(ri._SECTION_HEAD.search(tagline)),
         "tagline ran past its section heading")


def check_career(career: list[dict], warnings: list[str]) -> None:
    flag(warnings, not career, "no engagements found at all")
    seen: set[str] = set()
    for entry in career:
        label = entry["label"]
        flag(warnings, label in seen, f"duplicate label {label!r}")
        seen.add(label)
        flag(warnings, not label, "an engagement has an empty label")
        flag(warnings, bool(label) and label[0].islower(),
             f"label {label!r} starts lowercase, so it is probably a prose fragment")
        flag(warnings, len(label) >= 92, f"label {label!r} hit the 92-char trim")
        # A label containing a label-stop is a label that reached back past a
        # boundary into the bullet above it: the fraternity-glued-to-a-school
        # shape. `_LABEL_STOP` itself rather than a second pattern that means
        # roughly the same thing, because a reworded copy of this rule would
        # drift, and the first draft of it flagged "B.S. Computer Science" on
        # every run, which is exactly the initials case _LABEL_STOP excludes.
        flag(warnings, bool(ri._LABEL_STOP.search(label)),
             f"label {label!r} spans a label boundary")
        flag(warnings, entry["kind"] not in sp.KINDS,
             f"{label!r} has kind {entry['kind']!r}, which the arc cannot colour")
        flag(warnings, sp.month_index(entry["end"]) < sp.month_index(entry["start"]),
             f"{label!r} ends before it starts")
        flag(warnings, not entry["bullets"], f"{label!r} recovered no bullets")
        for bullet in entry["bullets"]:
            flag(warnings, bool(ri._PHONE.search(bullet)),
                 f"a bullet of {label!r} still carries a phone number")
            flag(warnings, bool(ri._EMAIL.search(bullet)),
                 f"a bullet of {label!r} still carries an email")
            flag(warnings, bool(ri._SECTION_HEAD.search(bullet)),
                 f"a bullet of {label!r} ran into a section heading")


def report(path: Path) -> list[str]:
    print(f"\n{'=' * 78}\n{path.name}\n{'=' * 78}")
    text = load(path)
    if text is None:
        return [f"{path.name}: unreadable"]

    warnings: list[str] = []

    found = ri.parse_contact(text)
    links = ri.read_pdf_links(path) if path.suffix.lower() == ".pdf" else []
    print("\n-- Identity --")
    for key in ("name", "role", "email", "location"):
        print(f"  {key:9} {found.get(key) or '(not found)'}")
    print(f"  {'tagline':9} {(found.get('tagline') or '(not found)')[:90]}")
    for link in found.get("links", []):
        print(f"  link      {link['label']}: {link['url']}  (from the text)")
    for link in links:
        print(f"  link      {link['label']}: {link['url']}  (from a link annotation)")
    if not found.get("links") and not links:
        print("  link      (none: no URL in the text and no link annotations)")
    check_contact(found, warnings)

    skills = ri.parse_skill_section(text)
    source = "the résumé's own skills section"
    if not skills:
        skills = ri.scan_catalog(text)
        source = "the built-in catalogue (no skills section found)"
    flat = [s for members in skills.values() for s in members]
    print(f"\n-- Skills: {len(flat)} in {len(skills)} group(s), from {source} --")
    for group, members in skills.items():
        print(f"  {group:26} {', '.join(members)}")
    flag(warnings, not skills, "no skills found by either route")
    flag(warnings, len(skills) > sp.MAX_SKILL_GROUPS,
         f"{len(skills)} groups, past MAX_SKILL_GROUPS, so two share a colour")

    career = ri.parse_engagements(text, flat)
    print(f"\n-- Engagements: {len(career)} --")
    for entry in career:
        print(f"\n  [{entry['kind']:9}] {entry['start']}..{entry['end']}  {entry['label']}")
        print(f"       skills: {', '.join(entry['skills']) or '(none matched)'}")
        for bullet in entry["bullets"]:
            print(f"       - {bullet[:100]}")
    check_career(career, warnings)

    # The two checks setup runs at the end, which read the collected data rather
    # than the PDF. Reused rather than reimplemented, for the usual reason.
    print("\n-- setup_profile's own checks --")
    sp.check_career_order(career)
    sp.report_orphan_skills(career, skills)
    sp.report_duplicate_skills(skills)

    print(f"\n-- Smells: {len(warnings)} --")
    for warning in warnings:
        print(f"  ! {warning}")
    if not warnings:
        print("  none")
    return [f"{path.name}: {w}" for w in warnings]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path,
                        help="résumé PDFs (or text files, with a loud caveat)")
    args = parser.parse_args()

    all_warnings: list[str] = []
    for path in args.paths:
        if not path.is_file():
            print(f"\nskipping {path}: not a file")
            all_warnings.append(f"{path}: not a file")
            continue
        all_warnings += report(path)

    print(f"\n{'=' * 78}")
    print(f"{len(args.paths)} file(s), {len(all_warnings)} smell(s) total")
    for warning in all_warnings:
        print(f"  ! {warning}")
    return 1 if all_warnings else 0


if __name__ == "__main__":
    sys.exit(main())
