#!/usr/bin/env python3
"""Interactive setup for profile.json.

Run once after forking, before your first deploy:

    python3 scripts/setup_profile.py

It opens by offering to read a résumé PDF, and everything it finds there
becomes the default answer to a prompt further down: name, role, email,
location, links, the tagline, the skill groups, the engagements, and one
bullet list per engagement for knowledge/resume.md. That is the quick path, and
it is first because a résumé already answers most of what follows.

Then prompts for identity, contact links, the stats band, the highlights
carousel, the skill groups, the career history, and the results chart, writes
profile.json, and rebuilds the two generated figures.

Safe to re-run: existing values are offered as defaults, so pressing Enter
through the whole thing leaves profile.json unchanged. That is a real contract
and worth keeping. Two things it depends on:

* The profile is updated from a copy of what was already on disk, not rebuilt
  from scratch, so keys this script does not manage survive. `_disabled_results`
  is the live example, and rebuilding the dict deleted it.
* An empty `results` is not written, since adding the key to a profile that
  lacked one is a change for no benefit.

Never edits an existing knowledge/ file. It writes the three core ones when they
are absent, and reads the rest at the end to name any file still describing the
previous owner: that corpus is what the chatbot answers as, and a fork that
fills in every prompt but leaves it alone looks finished while being wrong.

Nothing written into knowledge/ is generated prose. The résumé bullets are the
forker's own text, lifted verbatim and scrubbed of phone numbers and emails,
because that file is rendered publicly and fed to retrieval.
"""

import json
import re
import shutil
import sys
from pathlib import Path

# This file is imported three ways: run as a script, as `setup_profile` by
# tools/ and tests/test_resume_import.py, and as `scripts.setup_profile` by
# edit_profile.py and the other tests. Only the first two put scripts/ on the
# path, so the third would fail to find its sibling without this.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from resume_import import (  # noqa: E402
    SKILL_CATALOG,
    parse_contact,
    parse_engagements,
    parse_skill_section,
    read_pdf_links,
    read_pdf_text,
    scan_catalog,
    scrub_private,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT_DIR / "profile.json"
STATIC_DIR = ROOT_DIR / "static"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"

# Seeded when no skills exist yet, so a fresh checkout still renders the
# Skillset network instead of silently dropping the section. Meant to be
# replaced, not kept.
DEFAULT_SKILLS = {"Core Skills": ["Teamwork", "Leadership", "Communication", "Punctual"]}

# The three kinds the Career Arc knows how to colour. Anything else falls to the
# generator's KIND_FALLBACK and renders in the wrong hue, which is why this is a
# closed choice rather than free text. Keep in step with KIND_CLASS in
# tools/build_career_arc.py.
KINDS = ("education", "work", "project")

# The stylesheet defines --c1..--c6 and app.js's palette() wraps with `% 6`, so a
# seventh skill group silently reuses the first group's colour in the network,
# the legend, and the career arc's skill ring.
MAX_SKILL_GROUPS = 6

# The masthead cloud's shapes, and the one place a forker meets them.
#
# Keep in step with FORMS in static/masthead3d.js, the same way KINDS is kept in
# step with the arc builder. That file is the authority on what a name does;
# this is the authority on what a forker is told it does, and
# tests/test_profile.py fails when the two name sets drift. The registry drops a
# name it does not know and falls back to its own cycle, so drift costs a chosen
# shape rather than the header, but it costs it silently.
#
# Order is the order they are offered. The three that ship are first because
# they won two rounds of side by side renders against the rest; everything below
# them is built and tuned and lost one. "Lost" is a judgement about this page,
# not about the shape, which is exactly why they are still on the menu.
MASTHEAD_FORMS: dict[str, str] = {
    "horizon": "Ground with a curved far edge, like a limb seen from above",
    "wave": "A rolling surface of ground under the type",
    "torus": "A flat ring lying around the type, seen from slightly above",
    "plane": "A level ground plane running off both sides in perspective",
    "basin": "A bowl the type sits down inside",
    "rift": "Ground split by a canyon that converges in the distance",
    "crest": "Ground rearing into a breaking wave on one side",
    "terraces": "Three filled shelves at rising heights, dark bands between",
    "ripples": "Four concentric rings lying on the ground plane",
    "pool": "A bounded ellipse on the ground, dense at its rim",
    "wake": "Two arms diverging from a point in the distance",
    "canopy": "Mass hanging from the top of the frame, arching over the name",
    "crescent": "A shallow arc across the lower frame, tapering at both ends",
    "plume": "A column rising and spreading out of the left margin",
    "murmuration": "A swept swarm: dense head low left, tail rising right",
    "gate": "Two curved masses at the sides, the type standing in the gap",
    "network": "Four layers of nodes with their connections drawn in",
    "helix": "A double helix running the width of the header",
    "rings": "Three interlocking links laid across the header like a chain",
    "galaxy": "Two spiral arms and a core, face on to the camera",
    "sphere": "An even shell of grains. The neutral one",
    "knot": "A closed loop that passes through itself",
    "star": "A spiky, symmetric solid",
    "lorenz": "The Lorenz attractor: the butterfly",
    "thomas": "The Thomas attractor: an isotropic tangle",
    "aizawa": "The Aizawa attractor: a spindle wound around a sphere",
}

# How many ship, and therefore where the menu's first block ends.
DEFAULT_MASTHEAD_FORMS = ["horizon", "wave", "torus"]

# One form is held for 7s and takes 3s to become the next, so a cycle of N is a
# 10N second wait for any given shape in the worst case. Three is 30s, which is
# already about as long as a visitor is on the page. Past six the cloud is
# showing a shape most visitors will never see, which is a strange thing to
# spend a choice on; it is a warning and not a refusal because it is the
# forker's header.
SOFT_MAX_MASTHEAD_FORMS = 6



def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def prompt_yn(label: str, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    value = input(f"{label}{suffix}: ").strip().lower()
    if not value:
        return default
    return value.startswith("y")


def prompt_choice(label: str, choices: tuple[str, ...], default: str = "") -> str:
    """Ask until the answer is one of `choices`. Accepts unambiguous prefixes."""
    options = "/".join(choices)
    while True:
        value = prompt(f"{label} ({options})", default).strip().lower()
        if value in choices:
            return value
        matches = [c for c in choices if c.startswith(value)] if value else []
        if len(matches) == 1:
            return matches[0]
        print(f"    Please answer with one of: {options}")


def month_index(ym: str) -> int:
    """"2024-08" -> absolute month number. Mirrors months() in the arc builder."""
    year, month = ym.split("-")
    return int(year) * 12 + int(month) - 1


def prompt_month(label: str, default: str = "") -> str:
    """Ask until the answer is a real YYYY-MM.

    Deliberately strict, and deliberately does not accept "present". The arc
    builder parses these with a bare split and int(), so anything else does not
    degrade, it raises during the build.
    """
    while True:
        value = prompt(label, default).strip()
        try:
            year, month = value.split("-")
            if len(year) == 4 and 1 <= int(month) <= 12:
                int(year)
                return f"{int(year):04d}-{int(month):02d}"
        except (ValueError, AttributeError):
            pass
        print("    Use YYYY-MM, for example 2025-06. For an ongoing role use the current month.")


def collect_links(existing: list[dict]) -> list[dict]:
    print("\n-- Links (GitHub, LinkedIn, personal site, etc.) --")
    links: list[dict] = []
    for link in existing:
        print(f"  Current: {link.get('label')} -> {link.get('url')}")
        if not prompt_yn(f"  Keep '{link.get('label')}'?", default=True):
            continue
        label = prompt("  Label", link.get("label", ""))
        url = prompt("  URL", link.get("url", ""))
        if label and url:
            links.append({"label": label, "url": url})
    while prompt_yn("Add another link?", default=not links):
        label = prompt("  Label (e.g. GitHub)")
        url = prompt("  URL")
        if label and url:
            links.append({"label": label, "url": url})
        elif not label and not url:
            # Blank on both is how a person presses Enter out of this loop, and
            # it has to end it rather than skip: the loop's default is yes while
            # the list is empty, so a skip asks the same question again and
            # answers it yes again, forever. Same bug as the bio "Add an item?"
            # loop, on the same fresh-fork path the README calls safe to press
            # Enter through.
            break
        else:
            print("  Skipped, both label and URL are required.")
    return links


def collect_stats(existing: list[dict]) -> list[dict]:
    """The flat stats band. Only used when no highlight card carries a stat of
    its own; the card form is the one the prompts above offer first."""
    print("\n-- Stats band (the bold numbers, e.g. '47.3 FPS') --")
    print("  Leave the value blank at any point to stop adding entries.")
    print("  Prefix and suffix render hard against the number, so they are")
    print("  symbols. What the number measures is the Label: a prefix of")
    print("  'Employees' renders 'Employees10'.")
    stats: list[dict] = []
    for stat in existing:
        print(f"  Current: {stat.get('prefix', '')}{stat.get('value')}{stat.get('suffix', '')} "
              f"- {stat.get('label')}")
        if prompt_yn(f"  Keep this stat?", default=True):
            stats.append(stat)
    while True:
        raw_value = prompt("  Value (number, blank to stop)")
        if not raw_value:
            break
        try:
            value: float | int = float(raw_value)
            if value.is_integer():
                value = int(value)
        except ValueError:
            print("  Not a number, try again.")
            continue
        prefix = prompt("  Prefix (optional, e.g. '$')", "")
        suffix = prompt("  Suffix (optional, e.g. '+' or '%')", "")
        label = prompt("  Label (what the number measures, e.g. 'Employees')")
        if prefix or suffix:
            print(f"  Renders as: {prefix}{value}{suffix}  ({label})")
        stats.append({"value": value, "prefix": prefix, "suffix": suffix, "label": label})
    return stats


def collect_highlights(existing: list[dict]) -> list[dict]:
    print("\n-- Highlights carousel (project/experience cards) --")
    highlights: list[dict] = []
    for card in existing:
        print(f"  Current: {card.get('title')}")
        if prompt_yn("  Keep this card?", default=True):
            highlights.append(card)
    while prompt_yn("Add another highlight card?", default=not highlights):
        title = prompt("  Title")
        if not title:
            # Blank title ends the loop. See collect_links: skipping instead
            # re-asks a question whose default is yes while the list is empty.
            break
        body = prompt("  Body (a sentence or two)")
        if not body:
            print("  Skipped, title and body are required.")
            continue
        url = prompt("  Repository/project URL (optional)", "")
        card = {"title": title, "body": body}
        if url:
            card["url"] = url
        stat = collect_card_stat()
        if stat:
            card["stat"] = stat
        highlights.append(card)
    return highlights


def collect_card_stat() -> dict | None:
    """One optional bold number for a card. Cards with one make up the stat band,
    in carousel order and in the card's color, so the band and the carousel
    cannot drift apart. Leave the value blank for no tile."""
    raw_value = prompt("  Headline number for this card (blank for none)", "")
    if not raw_value:
        return None
    try:
        value: float | int = float(raw_value)
        if value.is_integer():
            value = int(value)
    except ValueError:
        print("  Not a number, no tile for this card.")
        return None
    # Both sit hard against the number with no space, so they are symbols and
    # not words. A real run answered "Employees" here and the page rendered
    # "Employees10"; what that answer wanted was the Label prompt below.
    print(f"  Prefix and suffix render against the number: <prefix>{value}<suffix>.")
    print("  Symbols, not words. What the number measures is the Label.")
    prefix = prompt("  Prefix (optional, e.g. '$')", "")
    suffix = prompt("  Suffix (optional, e.g. '+' or '%')", "")
    label = prompt("  Label (what the number measures, e.g. 'Employees')")
    if prefix or suffix:
        print(f"  Renders as: {prefix}{value}{suffix}  ({label})")
    return {"value": value, "prefix": prefix, "suffix": suffix, "label": label}


def collect_starter_questions(existing: list[str]) -> list[str]:
    print("\n-- Starter questions (suggestion chips under the chat) --")
    questions = list(existing)
    if questions:
        print("  Current:")
        for q in questions:
            print(f"    - {q}")
        if not prompt_yn("  Keep these?", default=True):
            questions = []
    while prompt_yn("Add a starter question?", default=not questions):
        q = prompt("  Question")
        if not q:
            # See collect_links: an empty answer has to end the loop, because
            # the loop's own default is yes while the list is empty.
            break
        questions.append(q)
    return questions


def collect_bio(existing: dict) -> dict:
    """The masthead's three rows: credential, work shipped, one closing line.

    Distinct from the tagline, which stays prose because it is still the page's
    <meta description> and the quote written into the corpus. This is the visible
    header, and it is set as rows because a paragraph at body size over the
    particle cloud needs a backdrop to survive and three short rows do not.

    Returning `{}` is a supported answer, not a failure: the template falls back
    to rendering the tagline as a paragraph, so pressing Enter through this
    section leaves a fork with a working header.

    The middle row is optional on its own, and this profile ships without it. So
    the fallback triggers on the whole block being empty rather than on the lead
    specifically, which is what it used to key on. Keying it on the lead meant
    answering the credential and closing prompts and then silently getting
    neither, because one skipped question discarded the other two answers.
    """
    print("\n-- Masthead bio (the rows under your name) --")
    print("  Leave all three empty to fall back to showing your tagline.")
    bio = dict(existing)
    meta = prompt("  Credential line (small caps, e.g. degree and year)", bio.get("meta", ""))
    lead = list(bio.get("lead", []))
    if lead:
        print("  Current middle row:")
        for item in lead:
            print(f"    - {item}")
        if not prompt_yn("  Keep these?", default=True):
            lead = []
    print("  Middle row: the things you have shipped, two to four of them.")
    print("  They are set on one line at display size, so keep each one short.")
    print("  Optional. Skip it if your highlight cards already say the same thing.")
    # An empty item ends the row rather than being skipped. Skipping it hung the
    # script: with `lead` empty the yes/no default is yes, so pressing Enter
    # asked again, and Enter through both prompts never terminated. That is the
    # path a fresh fork takes, and the one the README calls safe to press Enter
    # through, so the loop has to exit on the answer a person gives to skip it.
    while len(lead) < 4 and prompt_yn("  Add an item?", default=not lead):
        item = prompt("    Item")
        if not item:
            break
        lead.append(item)
    close = prompt("  Closing line (one sentence)", bio.get("close", ""))
    return {"meta": meta, "lead": lead, "close": close} if (meta or lead or close) else {}


def collect_masthead_forms(existing: list) -> list[str]:
    """Pick the shapes the masthead cloud cycles between, in order.

    Order matters and is kept: it is the order they morph in, and a cycle reads
    better when consecutive shapes differ by *composition* — something below the
    type, then behind it, then around it — rather than by outline alone. The
    shipped trio is that argument's answer, which is why it is offered whole as
    the default rather than as three separate suggestions.

    Anything not in MASTHEAD_FORMS is dropped here rather than written and
    dropped later by the browser, so a mistyped name is visible while the person
    who typed it is still looking.
    """
    print("\n-- Masthead shapes (what the particle cloud morphs between) --")
    print("  The default cycles three: something under the headline, something")
    print("  behind it, something around it. Press Enter to keep it.")

    names = list(MASTHEAD_FORMS)
    width = max(len(n) for n in names)
    for i, name in enumerate(names, start=1):
        if i == len(DEFAULT_MASTHEAD_FORMS) + 1:
            print("\n  Also available, built and tuned, cut from this page's cycle:")
        print(f"    {i:>2} {name:<{width}}  {MASTHEAD_FORMS[name]}")

    current = [n for n in existing if n in MASTHEAD_FORMS] or list(DEFAULT_MASTHEAD_FORMS)
    while True:
        raw = prompt("\n  Shapes, by number or name, in cycle order", ", ".join(current))
        picked: list[str] = []
        bad: list[str] = []
        for token in raw.replace(",", " ").split():
            if token.isdigit() and 1 <= int(token) <= len(names):
                choice = names[int(token) - 1]
            elif token.lower() in MASTHEAD_FORMS:
                choice = token.lower()
            else:
                bad.append(token)
                continue
            # Deliberately not a set: order is the cycle, and a repeat would
            # morph a shape into itself, which stalls the cloud for a whole turn.
            if choice not in picked:
                picked.append(choice)
        if bad:
            print(f"    Not a shape on the list: {', '.join(bad)}")
            continue
        if not picked:
            print("    Pick at least one. One shape is allowed: the cloud holds it and never morphs.")
            continue
        if len(picked) > SOFT_MAX_MASTHEAD_FORMS:
            print(f"    Note: {len(picked)} shapes is a {10 * len(picked)}s cycle, so a visitor may")
            print("    never see some of them. Keeping it anyway is fine.")
        print(f"    Cycle: {' -> '.join(picked)}")
        return picked


# ---------------------------------------------------------------------------
# Résumé import
#
# The readers and parsers are in scripts/resume_import.py. What stays here is
# the part that is setup's own: read_resume applies MAX_SKILL_GROUPS to what
# they propose, and import_resume asks before any of it becomes a default.
# ---------------------------------------------------------------------------


def read_resume(path: Path) -> dict | None:
    """Everything the import proposes from one PDF, with no prompts and no writes.

    Split out of import_resume so tools/score_resume_import.py grades exactly
    what setup would propose, not a restatement of it. inspect_resume_import
    restates it and had already drifted: it skips the MAX_SKILL_GROUPS trim.

    Returns None when the PDF cannot be read. Each career entry still carries
    its `bullets`; import_resume pops them off before they can reach
    profile.json.
    """
    text = read_pdf_text(path)
    if text is None:
        return None

    contact: dict = parse_contact(text)
    # Annotations after the text, and they win: a URL read off a link is the
    # real address, where one read out of the text is whatever fitted the line.
    annotated = read_pdf_links(path)
    if annotated:
        by_label = {link["label"]: link for link in contact.get("links", [])}
        by_label.update({link["label"]: link for link in annotated})
        contact["links"] = list(by_label.values())

    skills = parse_skill_section(text)
    source = "the résumé's own skills section"
    if not skills:
        skills = scan_catalog(text)
        source = "a scan against the built-in catalogue (no skills section found)"
    trimmed_from = 0
    if len(skills) > MAX_SKILL_GROUPS:
        trimmed_from = len(skills)
        skills = dict(sorted(skills.items(), key=lambda kv: -len(kv[1]))[:MAX_SKILL_GROUPS])

    flat_skills = [s for members in skills.values() for s in members]
    return {"text": text, "contact": contact, "skills": skills,
            "skills_source": source, "skills_trimmed_from": trimmed_from,
            "career": parse_engagements(text, flat_skills)}


def import_resume(existing: dict) -> dict:
    """Offer a résumé import and return a seed for the prompts that follow.

    This runs *first*, before Identity, because a résumé already answers most of
    what the prompts ask and answering them twice is the thing that made setup
    long. What comes back is a dict of proposals keyed like profile.json, plus
    `bullets`, which is corpus material and not a profile key.

    Returns `{}` when the import is declined or fails, which is what keeps the
    press-Enter-through-everything no-op contract intact for anyone who does not
    use this. The caller's existing values still win over every proposal, so a
    re-run with a PDF path typed in cannot overwrite curated answers either.
    """
    print("\n-- Start from your résumé (optional, and the quick path) --")
    print("  Reads a PDF you already have and proposes your name, role, contact")
    print("  details, links, skills and their groups, your engagements, and one")
    print("  bullet list per engagement for the chatbot's corpus. Everything it")
    print("  finds is shown as the default answer to a normal prompt below, so")
    print("  you approve or edit all of it before anything is written.")
    print("  Leave blank to skip and type the whole thing in by hand.")
    raw = prompt("  Path to résumé PDF (blank to skip)", "")
    if not raw:
        return {}

    path = Path(raw).expanduser()
    if not path.is_file():
        print(f"  Could not find {path}, skipping the import.")
        return {}

    proposal = read_resume(path)
    if proposal is None:
        return {}
    seed, skills, source, career = (proposal["contact"], proposal["skills"],
                                    proposal["skills_source"], proposal["career"])

    print("\n  Identity, from the header block:")
    for key in ("name", "role", "email", "location"):
        print(f"    {key:10} {seed.get(key) or '(not found, you will be asked)'}")
    for link in seed.get("links", []):
        print(f"    link       {link['label']}: {link['url']}")
    if seed.get("tagline"):
        print(f"    tagline    {seed['tagline'][:70]}...")

    if proposal["skills_trimmed_from"]:
        print(f"\n  Found {proposal['skills_trimmed_from']} groups, keeping the {MAX_SKILL_GROUPS} largest:")
        print(f"  past {MAX_SKILL_GROUPS} the colours repeat in both figures.")
    total = sum(len(v) for v in skills.values())
    print(f"\n  Skills: {total} in {len(skills)} group(s), from {source}.")
    for group, members in skills.items():
        print(f"    {group:26} {', '.join(members)}")

    # Off the entries before anything else can copy them: collect_career keeps a
    # kept entry verbatim, so a `bullets` key left here is written to
    # profile.json. Keyed by label, which is what render_resume_md joins on.
    bullets = {entry["label"]: entry.pop("bullets") for entry in career}
    print(f"\n  Engagements: {len(career)} found.")
    for entry in career:
        print(f"    {entry['kind']:10} {entry['start']}..{entry['end']}  {entry['label']}")
        print(f"      skills: {', '.join(entry['skills']) or '(none matched)'}")
        print(f"      bullets: {len(bullets[entry['label']])} for knowledge/resume.md")

    print("\n  These are proposals. Dates, skills and bullets come straight from")
    print("  the text, but 'kind' is guessed and anything the résumé did not")
    print("  spell out is missing, so read them once in the prompts that follow.")
    print("  Phone numbers and emails are stripped from everything headed for")
    print("  knowledge/, which is rendered publicly and read by the chatbot.")
    if not prompt_yn("  Use these as the starting point?", default=True):
        return {}

    # Existing values win, here and in main(). A re-run with a PDF path typed in
    # should not quietly replace groups that were curated by hand on a previous
    # pass, and that is also the whole no-op re-run contract.
    existing_skills = existing.get("skills") or {}
    merged_skills = dict(existing_skills)
    for group, members in skills.items():
        if group not in merged_skills:
            merged_skills[group] = members
    seed["skills"] = merged_skills
    seed["career"] = existing.get("career") or career
    seed["bullets"] = bullets
    seed["pdf_path"] = path
    return seed


def collect_skills(existing: dict) -> dict:
    """Collect the `skills` map: group name -> list of skills.

    Groups are disciplines. Each one becomes a colour in the Skillset network, a
    nebula blob behind it, a lobe of the particle cloud, and a band of the career
    arc's skill ring, so the count is not cosmetic (see MAX_SKILL_GROUPS).

    Insertion order is preserved, because group order picks which of --c1..--c6
    each discipline gets.
    """
    print("\n-- Skills (the coloured groups in the Skillset network) --")
    print("  Each group is a discipline. Pick from the menu by number, and type")
    print("  anything the menu does not have. Both go in the same list.")
    skills: dict[str, list[str]] = {}

    for group, members in existing.items():
        print(f"\n  Current group: {group}")
        print(f"    {', '.join(members)}")
        if not prompt_yn(f"  Keep '{group}'?", default=True):
            continue
        name = prompt("  Group name", group)
        picked = pick_skills_for_group(name, members, taken=skills)
        if name and picked:
            skills[name] = picked
        else:
            print("  Skipped, a group needs a name and at least one skill.")

    while prompt_yn("\nAdd another skill group?", default=not skills):
        print("\n  Suggested disciplines (or type your own name):")
        cats = list(SKILL_CATALOG)
        for i, cat in enumerate(cats, start=1):
            if cat not in skills:
                print(f"    {i} {cat}")
        raw = prompt("  Group name or number")
        if raw.isdigit() and 1 <= int(raw) <= len(cats):
            name = cats[int(raw) - 1]
        else:
            name = raw
        if not name:
            # See collect_links: an empty answer has to end the loop, because
            # the loop's own default is yes while there are no groups yet.
            break
        picked = pick_skills_for_group(name, [], taken=skills)
        if not picked:
            print("  Skipped, a group needs at least one skill.")
            continue
        skills[name] = picked
        if len(skills) > MAX_SKILL_GROUPS:
            print(f"  Note: past {MAX_SKILL_GROUPS} groups the colours repeat, so two")
            print("  disciplines will share a colour in the network and the legend.")

    return skills or dict(DEFAULT_SKILLS)


def pick_skills_for_group(
    group: str, current: list[str], taken: dict[str, list[str]]
) -> list[str]:
    """Build one group's skill list from the catalogue plus free text.

    `taken` is the groups already settled this run. A skill may only belong to
    one group: `place_skills` in the arc builder keys its output by skill name,
    so a name in two groups is silently overwritten and drawn in the second
    group's colour. Nothing errors and the figure looks fine, so it is caught
    here instead.

    Free text is accepted as-is rather than matched against the catalogue. The
    catalogue is a convenience, not a vocabulary, and refusing an unlisted skill
    would make it one.
    """
    menu = [s for s in SKILL_CATALOG.get(group, []) if s not in current]
    if menu:
        print(f"\n  Common {group} skills:")
        for i, skill in enumerate(menu, start=1):
            print(f"    {i:2} {skill}")
        print("    Enter numbers and/or your own names, comma separated.")

    default = ", ".join(current)
    raw = prompt("  Skills", default)

    out: list[str] = []
    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        if token.isdigit() and menu:
            n = int(token)
            if 1 <= n <= len(menu):
                token = menu[n - 1]
            else:
                print(f"    Ignored '{n}', the menu only goes to {len(menu)}.")
                continue
        owner = next((g for g, m in taken.items() if token in m), None)
        if owner:
            print(f"    Skipped '{token}', already in '{owner}'. A skill belongs")
            print("    to one group, since the group is what picks its colour.")
            continue
        if token not in out:
            out.append(token)
    return out


def flatten_skills(skills: dict) -> list[str]:
    """Every skill name, in group order, deduplicated."""
    out: list[str] = []
    for members in skills.values():
        for s in members:
            if s not in out:
                out.append(s)
    return out


def print_skill_menu(skills: dict) -> list[str]:
    """Print the numbered pick list, grouped by discipline. Returns the flat list."""
    flat = flatten_skills(skills)
    index = {s: i for i, s in enumerate(flat, start=1)}
    for group, members in skills.items():
        shown = "  ".join(f"{index[s]} {s}" for s in members if s in index)
        print(f"    {group}: {shown}")
    return flat


def prompt_skill_subset(flat: list[str], existing: list[str] | None = None) -> list[str]:
    """Pick skills by number, with free text allowed for anything not listed.

    Free text is kept rather than rejected, matching how the data already works:
    the arc builder draws a chord only for a skill that is also in the `skills`
    map and silently drops the rest, so an unknown name is a real, supported
    state. It is reported instead of refused, both here and in the summary at the
    end, so the drop is a choice rather than a surprise.
    """
    default = ", ".join(existing or [])
    raw = prompt("  Skills used (numbers, comma separated)", default)
    picked: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            n = int(token)
            if 1 <= n <= len(flat):
                if flat[n - 1] not in picked:
                    picked.append(flat[n - 1])
            else:
                print(f"    Ignored '{n}', no skill has that number.")
        elif token not in picked:
            picked.append(token)
            if token not in flat:
                print(f"    Note: '{token}' is not in your skill groups. It will be kept")
                print("    in the career data but will not draw a line in the figure.")
    return picked


def collect_career(existing: list[dict], skills: dict) -> list[dict]:
    """Collect the `career` list that the Career Arc is drawn from.

    Order in this list is load bearing twice over: the generator shades each
    engagement by its position among others of the same kind, and the HTML legend
    joins its chips to segments by index. So the keep-or-drop walk below preserves
    order and new entries are appended, never inserted.
    """
    print("\n-- Career (the engagements in the Career Arc figure) --")
    if not skills:
        print("  Skipped: define some skill groups first.")
        return list(existing)

    career: list[dict] = []
    for entry in existing:
        print(f"\n  Current: [{entry.get('kind', '?')}] {entry.get('start', '?')} to "
              f"{entry.get('end', '?')}  {entry.get('label', '?')}")
        if prompt_yn("  Keep this engagement?", default=True):
            career.append(entry)

    print("\n  Your skills, by number:")
    flat = print_skill_menu(skills)

    while prompt_yn("\nAdd another engagement?", default=not career):
        label = prompt("  Label (short, goes in the legend)")
        if not label:
            # See collect_links: an empty answer has to end the loop, because
            # the loop's own default is yes while the list is empty.
            break
        detail = prompt("  Detail (employer, school, or what it was)", "")
        kind = prompt_choice("  Kind", KINDS, "work")
        start = prompt_month("  Start (YYYY-MM)")
        while True:
            end = prompt_month("  End (YYYY-MM, use this month if ongoing)")
            if month_index(end) >= month_index(start):
                break
            print("    End is before start.")
        picked = prompt_skill_subset(flat)
        career.append({
            "label": label,
            "detail": detail,
            "kind": kind,
            "start": start,
            "end": end,
            "skills": picked,
        })

    return career


def check_career_order(career: list[dict]) -> None:
    """Warn when list order disagrees with date order within a kind.

    The generator derives each engagement's shade from its position among the
    same kind in this list, and shade is meant to read as recency. Nothing
    enforces that the list is actually in date order, so a newer entry appended
    after an older one renders as the more recent of the two regardless of when
    it happened. This is the one rule here that is a convention rather than a
    constraint, which is exactly why it is worth saying out loud.
    """
    by_kind: dict[str, list[tuple[str, str]]] = {}
    for entry in career:
        by_kind.setdefault(entry.get("kind", ""), []).append(
            (entry.get("start", ""), entry.get("label", "?"))
        )
    for kind, items in by_kind.items():
        starts = [s for s, _ in items]
        if starts != sorted(starts):
            print(f"\n  Heads up: your '{kind}' entries are not in date order.")
            print("  Shading in the figure reads as oldest to newest by list position,")
            print("  so it will not match the dates. Current order:")
            for start, label in items:
                print(f"    {start}  {label}")


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "(Aug 2022 to May 2026)", "(Mar 2023 to Present, Remote)", "(2024 to 2025)",
# "(Solo, 2026)". Only what this resume actually uses; a parser that accepts
# everything would also accept things that mean nothing here.
_RANGE = re.compile(
    r"\(([^)]*?)\)\s*$|\(([^)]*?)\)",
    re.IGNORECASE,
)
_DATE = re.compile(r"(?:([A-Za-z]{3})[a-z]*\s+)?((?:19|20)\d{2})|\b(present|current|ongoing)\b", re.I)


def _years_in(text: str) -> tuple[list[tuple[int, int | None]], bool]:
    """Every (year, month-or-None) in `text`, plus whether it says 'present'."""
    out: list[tuple[int, int | None]] = []
    open_ended = False
    for m in _DATE.finditer(text):
        if m.group(3):
            open_ended = True
            continue
        mon = _MONTHS.get((m.group(1) or "").lower()[:3])
        out.append((int(m.group(2)), mon))
    return out, open_ended


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def check_resume_dates(career: list[dict]) -> None:
    """Compare the career block against the dates written in knowledge/resume.md.

    Conventions says these two must agree: the arc and its legend are drawn from
    `career`, while resume.md renders on the page and grounds the chatbot. Until
    now nothing checked it, and the failure is a page that states two different
    dates for the same job in two places a visitor can see at once.

    This reports rather than refuses, for the same reason as the orphan check.
    The two files are not required to hold the same *set* of entries: an arc
    segment is a visualisation decision and a resume line is a career fact, and
    dropping an engagement from one is a legitimate reason to keep it in the
    other. What is never legitimate is the same engagement carrying two
    different date ranges.

    Matching is by token overlap against the `###` headings, not by position,
    since neither file has any reason to keep the other's order.
    """
    path = KNOWLEDGE_DIR / "resume.md"
    if not path.is_file():
        return
    headings = [
        line[4:].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("### ")
    ]
    if not headings:
        return

    problems: list[str] = []
    matched: set[str] = set()
    for entry in career:
        want = _tokens(entry.get("label", "")) | _tokens(entry.get("detail", ""))
        best, score = None, 0
        for h in headings:
            n = len(want & _tokens(h))
            if n > score:
                best, score = h, n
        if not best or score < 2:
            problems.append(
                f"    '{entry.get('label')}' is in profile.json but no resume.md heading matches it"
            )
            continue
        matched.add(best)

        found, open_ended = _years_in(best)
        years = {y for y, _ in found}
        s_year, s_mon = int(entry["start"][:4]), int(entry["start"][5:7])
        e_year = int(entry["end"][:4])
        if s_year not in years:
            problems.append(
                f"    '{entry.get('label')}' starts {entry['start']} in profile.json, "
                f"but resume.md says: {best}"
            )
        elif e_year not in years and not open_ended:
            problems.append(
                f"    '{entry.get('label')}' ends {entry['end']} in profile.json, "
                f"but resume.md says: {best}"
            )
        elif found:
            # Months only where the resume gives one, and matched by position:
            # the first date in the heading is the start and the last is the
            # end. Scanning for "any date in the start year" instead compares
            # the end month against the start month whenever an engagement
            # begins and ends in the same year, which is most projects.
            e_mon = int(entry["end"][5:7])
            pairs = [(found[0], s_year, s_mon, "starts")]
            if len(found) > 1 and not open_ended:
                pairs.append((found[-1], e_year, e_mon, "ends"))
            for (y, mon), want_y, want_m, word in pairs:
                if mon and y == want_y and mon != want_m:
                    problems.append(
                        f"    '{entry.get('label')}' {word} month {want_m:02d} in "
                        f"profile.json, resume.md says month {mon:02d}"
                    )

    if problems:
        print("\n  Dates disagree between profile.json and knowledge/resume.md.")
        print("  Both are shown on the page, and the chatbot is grounded in resume.md:")
        for p in problems:
            print(p)

    extra = [h for h in headings if h not in matched]
    if extra:
        print(f"\n  {len(extra)} resume.md heading(s) have no engagement in profile.json,")
        print("  so they are on the page and in the chatbot but not in the Career Arc:")
        for h in extra:
            print(f"    {h}")


def report_duplicate_skills(skills: dict) -> None:
    """Name any skill listed in more than one group.

    The group a skill sits in is what picks its colour, in the network, the
    legend and the arc's skill ring alike. `place_skills` in the arc builder
    keys its output dict by skill name, so a duplicate is silently overwritten
    and the mark is drawn in whichever group came last. Nothing errors, and the
    only symptom is one node wearing the wrong discipline's colour, which is
    close to unfindable by eye.

    The prompt already refuses to create these, so this catches hand edits.
    """
    owners: dict[str, list[str]] = {}
    for group, members in skills.items():
        for s in members:
            owners.setdefault(s, []).append(group)
    dupes = {s: gs for s, gs in owners.items() if len(gs) > 1}
    if dupes:
        print(f"\n  {len(dupes)} skill(s) appear in more than one group. Each will be")
        print("  drawn in the last group's colour, in both figures:")
        for s, groups in dupes.items():
            print(f"    {s:24} {' , '.join(groups)}")
        print("  Remove the duplicate from all but one group.")


def report_orphan_skills(career: list[dict], skills: dict) -> None:
    """Name the skill references that will not draw a chord."""
    known = set(flatten_skills(skills))
    orphans: list[str] = []
    total = 0
    for entry in career:
        for s in entry.get("skills", []):
            total += 1
            if s not in known and s not in orphans:
                orphans.append(s)
    if orphans:
        print(f"\n  {len(orphans)} skill name(s) in your career data are not in any skill")
        print(f"  group, so they will not draw a line in the figure (of {total} references):")
        print(f"    {', '.join(orphans)}")
        print("  Add them to a skill group if you want them drawn.")


def report_stale_knowledge(previous: dict, name: str, known_as: str) -> bool:
    """Name the knowledge/ files still describing the previous owner.

    This is the one gap setup cannot close by writing profile.json. The corpus in
    knowledge/ is what the chatbot is grounded in and what it answers as, so a
    fork that fills in every prompt still ships a page with the new owner's name
    on it and the previous owner's life inside the chat. Nothing errors and the
    page looks finished, which is what makes it worth a loud warning rather than
    the passing reminder that used to be here.

    Only the tokens that actually changed are searched. A forker who shares a
    first name with the previous owner would otherwise be told to scrub their own
    name out of a corpus they just wrote.

    Reads knowledge/ but never writes it. Scaffolding templates would mean
    writing into a directory that already has content, and being wrong there
    destroys work rather than merely failing to help.
    """
    if not KNOWLEDGE_DIR.is_dir():
        return False

    new_tokens = {t.casefold() for t in (name, known_as) if t}
    for whole in (name, known_as):
        new_tokens.update(part.casefold() for part in whole.split() if part)

    old_tokens: list[str] = []
    for whole in (previous.get("name", ""), previous.get("known_as", "")):
        for part in [whole, *whole.split()]:
            part = part.strip()
            # Two characters is initials and punctuation; matching those would
            # flag most of the English language.
            if len(part) > 2 and part.casefold() not in new_tokens:
                if part not in old_tokens:
                    old_tokens.append(part)
    if not old_tokens:
        return False

    pattern = re.compile(
        r"\b(" + "|".join(re.escape(t) for t in old_tokens) + r")\b", re.IGNORECASE
    )
    hits: list[tuple[Path, int]] = []
    for path in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        try:
            found = pattern.findall(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if found:
            hits.append((path.relative_to(ROOT_DIR), len(found)))
    if not hits:
        return False

    print("\n  " + "!" * 62)
    print(f"  {len(hits)} file(s) in knowledge/ still describe {previous.get('name', 'the previous owner')}.")
    print("  This is the chatbot's grounding corpus, so it will answer as them")
    print("  on a page that now carries your name, and nothing will look broken:")
    for path, count in hits:
        print(f"    {str(path):44} {count} mention(s)")
    print(f"  Searched for: {', '.join(old_tokens)}")
    print("  Replace these with your own before deploying.")
    print("  " + "!" * 62)
    return True


# ---------------------------------------------------------------------------
# knowledge/ scaffolding
#
# The one gap profile.json cannot close. A fork can fill in every prompt and
# still ship a page carrying the new owner's name with the previous owner's life
# inside the chat, because knowledge/ is what the chatbot is grounded in and
# answers as. report_stale_knowledge names those files; this writes the missing
# ones so there is something true to replace them with.
#
# Every write is guarded on the file not existing. Never a force flag and never
# an overwrite prompt: the objection to scaffolding at all was that it means
# writing into a directory that already has content, and a per file existence
# check is what answers it. A fork with a half written corpus gets the missing
# pieces and keeps the rest.
#
# The files contain facts and nothing else. Inventing achievements is the one
# thing this must never do: the whole pipeline exists to keep the assistant from
# saying things that are not in the corpus, so seeding the corpus with plausible
# filler would defeat it at the source.
#
# Guidance about what to write goes to the terminal, never into the files. An
# earlier version left `<!-- TODO -->` markers in them, which is invisible on the
# page and therefore looks harmless, but the chatbot ingests raw markdown: the
# marker becomes retrievable context, so "TODO: expand with your genuine
# motivation" sits in the index as a chunk competing with real answers. This
# repo's own corpus shipped that way for a while. A comment is not a safe place
# to put a note in a file that is also a retrieval source.
# ---------------------------------------------------------------------------

_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def pretty_month(ym: str) -> str:
    """"2024-08" -> "Aug 2024", the form the résumé headings already use."""
    try:
        year, month = ym.split("-")
        return f"{_MONTH_NAMES[int(month) - 1]} {int(year)}"
    except (ValueError, IndexError):
        return ym


def render_resume_md(profile: dict, bullets: dict[str, list[str]] | None = None) -> str:
    """Build resume.md from the career block, and from the résumé's own bullets.

    Dates are formatted from the same `start` and `end` the arc is drawn from,
    so the two places that must agree do so by construction and
    check_resume_dates cannot fire on a fresh fork. That is the whole reason
    this generates rather than prompting for a second copy of the same dates.

    **Nothing here is generated prose.** `bullets` is text lifted verbatim out of
    the forker's own PDF by `extract_bullets`, scrubbed of phone numbers and
    emails. That distinction is the whole rule: a *written* achievement is a
    fabricated one and this file is both rendered on the page and fed to
    retrieval, but the person's own résumé line is neither fabricated nor new.
    Without it a fresh fork's corpus is headings with dates under them, which
    retrieves as a question answered by silence and is why a new fork's chatbot
    sounds thin.

    Bullets are joined by label, so an engagement renamed at the career prompt
    simply has none: a miss is quiet and empty, never another entry's text.
    """
    bullets = bullets or {}
    name = profile.get("name", "")
    known_as = profile.get("known_as") or name.split(" ")[0] if name else "the owner"
    lines = [
        f"# {name} — Resume",
        "",
        "**Source: official resume. This file was scaffolded by "
        "scripts/setup_profile.py from the PDF and is worth reading once.**"
        if bullets else
        "**Source: official resume. This file was scaffolded by "
        "scripts/setup_profile.py and needs your detail added.**",
        "",
        "## Contact and headline",
        "",
        f"- Name: {name}",
    ]
    # Scrubbed, because the role is the one header field read out of the same
    # line as the contact details, and a role that ran on into them published a
    # street address here on two producers.
    if profile.get("role") and scrub_private(profile["role"]):
        lines.append(f"- Role: {scrub_private(profile['role'])}")
    if profile.get("location"):
        lines.append(f"- Location: {profile['location']}")
    # Deliberately no phone number, and no email unless it is already the one on
    # the public page: this file renders publicly and is fed to the chatbot.
    if profile.get("email"):
        lines.append(f"- Email: {profile['email']}")
    lines += ["", "## Profile", ""]
    if profile.get("tagline"):
        lines += [profile["tagline"], ""]

    headings = (("education", "Education"), ("work", "Experience"), ("project", "Projects"))
    for kind, title in headings:
        entries = [c for c in profile.get("career", []) if c.get("kind") == kind]
        if not entries:
            continue
        lines += [f"## {title}", ""]
        for entry in entries:
            span = f"{pretty_month(entry.get('start', ''))} to {pretty_month(entry.get('end', ''))}"
            detail = entry.get("detail", "")
            head = f"{entry.get('label', '')}"
            if detail:
                head = f"{head} — {detail}"
            lines += [f"### {head} ({span})", ""]
            for bullet in bullets.get(entry.get("label", ""), []):
                lines.append(f"- {bullet}")
            if entry.get("skills"):
                lines.append(f"- Skills used: {', '.join(entry['skills'])}.")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_about_me(profile: dict) -> str:
    name = profile.get("name", "")
    known_as = profile.get("known_as") or (name.split(" ")[0] if name else "the owner")
    groups = ", ".join(list(profile.get("skills", {}))[:4])
    snapshot = name + (f" is a {profile['role']}" if profile.get("role") else "")
    snapshot += f", based in {profile['location']}" if profile.get("location") else ""
    snapshot += "."
    # The tagline is written in the first person and this file is in the third,
    # so it is quoted rather than pasted. Spliced in raw it produced "X is an
    # engineer based in Portland. I build data pipelines...", which is the kind of
    # voice confusion the corpus then teaches the assistant.
    quoted = (
        f'\n\nIn {known_as}\'s own words: "{profile["tagline"].strip()}"'
        if profile.get("tagline") else ""
    )
    # They/them throughout. This script cannot know a forker's pronouns and a
    # name does not tell it, so the neutral form is the only correct default
    # and it needs no TODO asking anyone to go and fix a guess.
    # No empty sections either. A heading with nothing under it retrieves as a
    # chunk that answers its own question with silence, which is worse than the
    # section not existing: the assistant finds a "What drives them" heading and
    # has nothing to say beneath it. The terminal names the sections to add.
    return f"""# About {known_as}

**Source: personal background.**

## Snapshot

{snapshot}{quoted}

{f"Main areas of work: {groups}." if groups else ""}
"""


def render_roles_open_to(profile: dict, roles: list[str]) -> str:
    name = profile.get("name", "")
    known_as = profile.get("known_as") or (name.split(" ")[0] if name else "the owner")
    target = (
        f"""## Target roles

{known_as} is actively seeking roles in:

""" + "\n".join(f"- {r}" for r in roles) + "\n"
    ) if roles else ""
    logistics = (
        f"""
## Logistics and preferences

- Based in {profile['location']}.
"""
        if profile.get("location") else ""
    )
    return f"""# Roles {known_as} Is Open To

**Source: job search preferences.**

{target}{logistics}"""


def scaffold_knowledge(profile: dict, bullets: dict[str, list[str]] | None = None) -> None:
    """Write the missing knowledge/ files. Never touches one that exists."""
    print("\n-- Grounding corpus (knowledge/) --")
    KNOWLEDGE_DIR.mkdir(exist_ok=True)

    planned: list[tuple[Path, str]] = [
        (KNOWLEDGE_DIR / "resume.md", "resume"),
        (KNOWLEDGE_DIR / "about-me.md", "about"),
        (KNOWLEDGE_DIR / "roles-open-to.md", "roles"),
    ]
    missing = [(path, which) for path, which in planned if not path.exists()]
    if not missing:
        print("  All three core files already exist, nothing written.")
        print("  This only ever creates missing files; it never edits or replaces one.")
        return

    print("  These files are what the chatbot is grounded in, and it answers as")
    print("  whoever they describe. Missing:")
    for path, _ in missing:
        print(f"    {path.relative_to(ROOT_DIR)}")
    if not prompt_yn("  Write starter versions of them?", default=True):
        print("  Skipped. The chatbot cannot answer about you until these exist.")
        return

    roles: list[str] = []
    if any(which == "roles" for _, which in missing):
        print("\n  Target roles, one per line, blank to finish.")
        print("  These go into the summary injected on every chat call, so they")
        print("  shape every answer and not only the ones about your job search.")
        while True:
            role = prompt("    Role")
            if not role:
                break
            roles.append(role)

    renderers = {
        "resume": lambda: render_resume_md(profile, bullets),
        "about": lambda: render_about_me(profile),
        "roles": lambda: render_roles_open_to(profile, roles),
    }
    for path, which in missing:
        path.write_text(renderers[which](), encoding="utf-8")
        print(f"  Wrote {path.relative_to(ROOT_DIR)}")

    # The only place this guidance may live. Written into the files it would be
    # indexed and retrieved, so the assistant would answer out of the
    # instructions instead of out of the facts.
    print("\n  Facts only. Nothing was invented, because an achievement written")
    print("  here is one the chatbot then states as fact, and no section was left")
    print("  empty, because an empty heading retrieves as a question with silence")
    print("  under it. What is missing is missing, and these are worth adding:")
    if bullets:
        print("\n  resume.md carries your own résumé bullets, lifted verbatim and")
        print("  stripped of phone numbers and emails. Read them once: PDF text")
        print("  extraction runs lines together, and whatever is in that file is")
        print("  what the chatbot will say and what the page will show.")
    print("""
    resume.md         one bullet per engagement: what you did and what came
                      of it, with numbers where you have them
    about-me.md       what drives you, and how you work with other people
    roles-open-to.md  what you bring to the roles you listed, naming
                      specific work

  roles-open-to.md and resume.md carry the most weight: a summary built from
  them is injected into every chat answer, so thin text there weakens every
  reply and not only the ones about your job search.""")


def run_builders(profile: dict) -> None:
    """Rebuild the generated figures.

    Both are build artifacts, so writing profile.json alone leaves the page
    showing the previous run's data next to the new labels. Running them here
    removes a whole class of silent staleness that a printed reminder does not.
    """
    import subprocess

    jobs = []
    if profile.get("career"):
        jobs.append(("Career Arc", ROOT_DIR / "tools" / "build_career_arc.py"))
    if (profile.get("results") or {}).get("points"):
        jobs.append(("Results chart", ROOT_DIR / "tools" / "build_results_chart.py"))
    for name, script in jobs:
        print(f"  Rebuilding the {name}...", end=" ", flush=True)
        done = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True
        )
        if done.returncode == 0:
            print("done.")
        else:
            print("failed.")
            print((done.stderr or done.stdout).strip())
            print(f"  Run it by hand to see the full error: python3 {script}")


def collect_results(existing: dict) -> dict:
    """Collect the Results chart block.

    The one question that matters is how many numbers there are per option,
    because that is what picks the mark: two makes a scatter with a frontier,
    one makes ranked bars. The forker answers in their own terms ("FPS and
    accuracy" versus "just a score") and never has to name a chart type. The
    generator re-derives the same choice from the data, so a hand-edit that adds
    or drops a second number switches the chart without touching any config.
    """
    print("\n-- Results chart (measured numbers from one project) --")
    print("  Optional. This is the section that shows you measured something")
    print("  rather than asserted it. Leave the title blank to skip it entirely.")
    if existing.get("points"):
        print(f"  Current: {existing.get('title')} ({len(existing['points'])} points)")
        if prompt_yn("  Keep the existing results chart?", default=True):
            return existing

    title = prompt("  Section title (blank to skip)", "")
    if not title:
        return {}

    note = prompt("  One or two sentences on what the chart shows", "")
    two = prompt_yn("  Do you have TWO numbers per option (e.g. speed AND accuracy)?", default=False)
    x_label = prompt("  Label for the first number (e.g. 'Frames per second')")
    y_label = prompt("  Label for the second number (e.g. 'Accuracy')") if two else ""
    point_label = prompt("  What is being compared? (e.g. 'Model', 'Approach')", "Option")
    source = prompt("  Where the numbers came from (optional)", "")

    marker: dict = {}
    if prompt_yn("  Is there a threshold worth drawing (e.g. 30 FPS is real time)?", default=False):
        raw = prompt("    Threshold value")
        try:
            marker = {"value": float(raw), "label": prompt("    Threshold label", "target")}
        except ValueError:
            print("    Not a number, skipping the threshold line.")

    print("\n  Now the options. Leave the name blank to stop.")
    points: list[dict] = []
    while True:
        label = prompt("    Name (blank to stop)")
        if not label:
            break
        try:
            x = float(prompt(f"    {x_label or 'First number'}"))
            y = float(prompt(f"    {y_label or 'Second number'}")) if two else None
        except ValueError:
            print("    Not a number, skipping this option.")
            continue
        point: dict = {"label": label, "x": x}
        if y is not None:
            point["y"] = y
        note_p = prompt("    Note (optional, shows in the tooltip and table)", "")
        if note_p:
            point["note"] = note_p
        # Exactly one point may be highlighted. Later answers win rather than
        # producing two emphasized marks, which would make the emphasis mean
        # nothing.
        if prompt_yn("    Is this the one you picked / the winner?", default=False):
            for other in points:
                other.pop("highlight", None)
            point["highlight"] = True
        points.append(point)

    if not points:
        print("  No points entered, skipping the results chart.")
        return {}

    block: dict = {
        "title": title,
        "note": note,
        "x_label": x_label,
        "point_label": point_label,
        "frontier": two,
        "points": points,
    }
    if two:
        block["y_label"] = y_label
    if marker:
        block["x_marker"] = marker
    if source:
        block["source"] = source
    return block


def copy_resume_pdf(src: Path, current: str) -> str:
    """Offer the imported PDF as the file the Download button serves.

    The button's file is `resume_pdf` in profile.json and the template excludes
    `static/resume.pdf`, so a fresh fork has a Download button and nothing
    behind it. The forker has just handed over the exact file it wants, so
    asking for it a second time is the kind of step that makes setup feel long.

    `tools/strip_pdf_phone.py` runs over it first. It edits the content stream
    of the contact line, so it is layout sensitive and can decline to find
    anything; that is a reason to copy the original and say so, not a reason to
    skip the attempt, since the phone number is the one thing on a résumé that
    should not be published.
    """
    print("\n-- Download button --")
    print(f"  Current: {current or '(none)'}")
    print("  Your résumé PDF can be the file the page's Download button serves.")
    if not prompt_yn(f"  Use {src.name} for it?", default=not current):
        return current

    dest = STATIC_DIR / "resume.pdf"
    STATIC_DIR.mkdir(exist_ok=True)
    stripped = False
    try:
        tools_dir = str(ROOT_DIR / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import strip_pdf_phone

        stripped = strip_pdf_phone.main(src, dest) == 0
    # SystemExit explicitly, and this is not defensive padding: strip_pdf_phone
    # is a command line tool and reports "no phone-shaped run found" by raising
    # it. SystemExit derives from BaseException rather than Exception, so an
    # `except Exception` here does not catch it and a PDF with no phone number
    # in it, which is the *good* case, killed the whole setup run at this
    # prompt with every answer above it already typed and nothing written.
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - any PDF shape lands here
        print(f"  {exc}")
    if stripped and dest.exists():
        print(f"  Copied to {dest} with the phone number removed.")
    else:
        shutil.copyfile(src, dest)
        print(f"  Copied to {dest} unchanged.")
        print("  No phone number was removed. Check the PDF yourself: this file")
        print("  is served publicly. tools/strip_pdf_phone.py is the tool.")
    return f"/static/{dest.name}"


def copy_photo(current: str) -> str:
    print("\n-- Photo --")
    print(f"  Current: {current or '(none)'}")
    path_str = prompt("  Path to a local photo to use (blank to keep current)", "")
    if not path_str:
        return current
    src = Path(path_str).expanduser()
    if not src.is_file():
        print(f"  Could not find {src}, keeping current photo.")
        return current
    dest = STATIC_DIR / f"profilepic{src.suffix.lower()}"
    shutil.copyfile(src, dest)
    print(f"  Copied to {dest}")
    return f"/static/{dest.name}"


def main() -> None:
    existing: dict = {}
    if PROFILE_PATH.exists():
        existing = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        print(f"Found existing {PROFILE_PATH.name}, press Enter on any prompt to keep its value.\n")
    else:
        print(f"No {PROFILE_PATH.name} found, starting fresh.\n")

    # The résumé import runs first and pre-fills the prompts below, because a
    # résumé already answers most of them and answering them twice is what made
    # this long. It returns {} when declined or when the PDF cannot be read, and
    # every prompt below falls back to exactly what it used before, so the
    # no-op re-run contract holds on both paths.
    seed = import_resume(existing)

    def seeded(key: str, fallback: str = "") -> str:
        """Existing value, then the résumé's proposal, then the fallback.

        Existing first is load bearing: a re-run with a PDF path typed in must
        not overwrite an answer that was curated by hand, and a re-run that
        changed nothing must leave profile.json byte-identical.
        """
        return existing.get(key) or seed.get(key) or fallback

    print("\n-- Identity --")
    name = prompt("Full name", seeded("name"))
    known_as = prompt("Informal first name used by the chatbot", existing.get("known_as", name.split(" ")[0] if name else ""))
    role = prompt("Role / title", seeded("role"))
    tagline = prompt("Tagline (a sentence or two about you)", seeded("tagline"))
    email = prompt("Email (leave blank to keep it off the public page)", seeded("email"))
    location = prompt("Location", seeded("location"))

    photo = copy_photo(existing.get("photo", ""))
    resume_pdf = existing.get("resume_pdf", "")
    if seed.get("pdf_path"):
        resume_pdf = copy_resume_pdf(seed["pdf_path"], resume_pdf)
    bio = collect_bio(existing.get("bio", {}) or {})
    links = collect_links(existing.get("links") or seed.get("links", []))
    highlights = collect_highlights(existing.get("highlights", []))
    # The flat band is only offered when no card took a number, since a band
    # built from the cards would ignore it anyway (stats_from in app/profile.py).
    cards_carry_stats = any(c.get("stat") for c in highlights)
    stats = [] if cards_carry_stats else collect_stats(existing.get("stats", []))
    starter_questions = collect_starter_questions(existing.get("starter_questions", []))
    masthead_forms = collect_masthead_forms(existing.get("masthead_forms", []))
    # Skills before career: the career prompt offers skills as a numbered menu,
    # so the groups have to exist before it can show anything worth picking from.
    # import_resume has already merged these against `existing`, existing first.
    skills = collect_skills(seed.get("skills") or existing.get("skills") or {})
    career = collect_career(seed.get("career") or existing.get("career", []), skills)
    results = collect_results(existing.get("results", {}) or {})

    # Start from what was already there so that keys this script does not manage
    # survive a re-run. `_disabled_results` is the live example: the results block
    # is parked under that name, and rebuilding the dict from scratch deleted it.
    # Anything hand-added to profile.json has the same problem, and losing it
    # silently is the worst possible failure for a tool whose contract is that it
    # is safe to run again.
    profile = dict(existing)
    profile.update({
        "name": name,
        "known_as": known_as,
        "role": role,
        "photo": photo,
        "tagline": tagline,
        "bio": bio,
        "email": email,
        "location": location,
        "links": links,
        "highlights": highlights,
        "starter_questions": starter_questions,
        "masthead_forms": masthead_forms,
        "skills": skills,
        "career": career,
    })
    # Only write `results` when there is something in it. Writing an empty object
    # would add a key to a profile that did not have one, which breaks a
    # no-op re-run for no benefit: app/profile.py's _DEFAULTS already treats a
    # missing block and an empty one identically, and both hide the section.
    if results:
        profile["results"] = results
    # `stats` is the same guard, with one extra case. An empty list written to a
    # profile that had no `stats` key breaks the no-op re-run for nothing. But
    # empty means two different things here: the band was offered and left empty,
    # which should clear it, or it was never offered because the cards carry the
    # numbers, in which case an existing flat list is dormant rather than dead
    # (stats_from honours it again the moment no card has a number) and clearing
    # it would be silent data loss.
    if stats:
        profile["stats"] = stats
    elif not cards_carry_stats:
        profile.pop("stats", None)
    # Same guard again: only written when there is a file behind it. Adding
    # `resume_pdf` to a profile that never had one would put a Download button
    # on the page pointing at a 404.
    if resume_pdf:
        profile["resume_pdf"] = resume_pdf

    # ensure_ascii=False, or every non-ASCII character already in the file comes
    # back as an escape: `·` in bio.meta was rewritten to `·` on every run,
    # which is the same string to a JSON reader and a diff to a human.
    PROFILE_PATH.write_text(
        json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nWrote {PROFILE_PATH}")

    check_career_order(career)
    check_resume_dates(career)
    report_duplicate_skills(skills)
    report_orphan_skills(career, skills)

    run_builders(profile)

    # Before the stale scan, not after: scaffolding first means the scan runs
    # against the files that now exist, so the two agree instead of the warning
    # naming a file this just created.
    scaffold_knowledge(profile, seed.get("bullets"))

    stale = report_stale_knowledge(existing, name, known_as)

    if stale:
        print("\nNext: fix the knowledge/ files listed above, then add your")
        print("GROQ_API_KEY to .env before running the app.")
    else:
        print("\nNext: check that knowledge/ describes you, and add your")
        print("GROQ_API_KEY to .env before running the app.")
    if career:
        print("Keep the dates in knowledge/resume.md in step with the career block:")
        print("both are shown on the page. check_resume_dates above compares them,")
        print("but it reports rather than refuses, so anything it printed is yours to fix.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nAborted, no changes written.")
        sys.exit(1)


