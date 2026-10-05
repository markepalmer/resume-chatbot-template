#!/usr/bin/env python3
"""Read a résumé PDF and propose what setup would otherwise ask for.

Split out of scripts/setup_profile.py, which still owns everything interactive:
`read_resume` there composes these parsers and applies setup's own limits, and
`import_resume` asks before any proposal becomes a default. Nothing in this
module prompts, and nothing in it writes a file.

The reasoning behind every rule below, and the traps each one closed, is in
docs/onboarding.md under "Résumé import". The failures are pinned in
tests/test_resume_import.py.
"""

import datetime as _dt
import re
import unicodedata
from pathlib import Path

# Starter menu for setup_profile.py's skills prompt. Not a whitelist: anything
# can be typed in, and these are only here so the common case is picking numbers
# rather than recalling forty tool names from memory and typing each one
# correctly. It lives in this file because the import also matches against it
# (canonical_skill, scan_catalog), and setup_profile.py importing it from here
# is the direction that does not make the two files import each other.
#
# The category names are suggestions too. What they encode is the granularity
# that reads well in the two figures: one line per thing a person would claim,
# not one per library. A catalogue of forty specific tools would push a forker
# straight into the readability problem these figures already have, where most
# skills touch a single engagement and the chord diagram degenerates into spokes.
SKILL_CATALOG: dict[str, list[str]] = {
    "Machine Learning": [
        "Machine Learning", "PyTorch", "TensorFlow", "scikit-learn", "CNNs",
        "Transformers", "Model Training", "Model Eval", "MLOps",
    ],
    "LLMs & Agents": [
        "LLMs", "LangChain", "RAG", "Embeddings", "Prompt Engineering",
        "RLHF", "Fine Tuning", "Agents", "Vector Search",
    ],
    "Computer Vision": [
        "Computer Vision", "OpenCV", "Image Classification", "Object Detection",
        "Pose Estimation", "Segmentation", "Edge Deploy",
    ],
    "Data & Infrastructure": [
        "Data Science", "Data Engineering", "SQL", "PostgreSQL", "Statistics",
        "Data Visualization", "NumPy", "Spark", "Airflow", "Docker",
        "Kubernetes", "AWS", "GCP", "CI/CD", "Git",
    ],
    "Web & Mobile": [
        "React", "Next.js", "Node.js", "Django", "FastAPI", "REST APIs",
        "GraphQL", "Swift", "SwiftUI", "Kotlin", "MongoDB", "Redis",
    ],
    "Languages": [
        "Python", "JavaScript", "TypeScript", "Java", "C++", "Go", "Rust",
        "Swift", "C#", "Ruby", "Bash", "R",
    ],
    "Design & Product": [
        "Figma", "UI Design", "UX Research", "Accessibility", "Prototyping",
        "Technical Writing", "Product Strategy",
    ],
}


# ---------------------------------------------------------------------------
# Résumé import
#
# Reads a PDF the forker already has and *proposes* skills, groups and
# engagements. Nothing here writes profile.json. The proposals are handed to the
# ordinary collect_* prompts as pre-filled answers, so one code path still does
# the writing and the checks at the end cannot be skipped by importing.
#
# Deterministic on purpose. The only genuinely semantic step is deciding which
# discipline a skill belongs to, and a résumé that has a skills section has
# already answered that in the author's own words; a model asked to group them
# would be overwriting a real answer with a guess. It would also make setup
# require a network call and an API key before the site exists.
#
# pypdf is imported lazily and is deliberately not in requirements.txt: this
# script runs on a laptop and never in the container, so a dependency only it
# needs has no business in the image or the lockfile.
# ---------------------------------------------------------------------------

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}

# A date range is the one thing every résumé writes the same way, which makes it
# a better anchor for "an engagement starts here" than any heading pattern.
#
# Named for the PDF deliberately. This was `_RANGE` first, which is also the
# name check_resume_dates in setup_profile.py uses for parenthesised spans in
# resume.md headings. While both lived in one module, Python rebound the name at
# module scope without a word, so the later definition won and this parser
# silently ran on a regex that matches "(678)" and "(A)": every engagement came
# back with a None month.
_PDF_RANGE = re.compile(
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(\d{4})"
    r"\s*(?:-|to|through|–|—)\s*"
    r"(present|current|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{4})",
    re.I,
)

# A month written as a number: "03/2021 - present", "06/2019 – 02/2021".
#
# A sibling of _PDF_RANGE rather than a branch inside it. _PDF_RANGE is the
# anchor every fix in this file was measured against, and its groups are read
# by position in _month_anchors, so widening it moves both at once. Nothing on
# the owner's résumé is written this way, which is also why it was missed.
_PDF_NUMERIC = re.compile(
    r"(?<![\d/])(0?[1-9]|1[0-2])/((?:19|20)\d{2})"
    r"\s*(?:-|to|through|–|—)\s*"
    r"(present|current|(0?[1-9]|1[0-2])/((?:19|20)\d{2}))(?![\d/])",
    re.I,
)


# A project dated by year rather than by month: "2026", "2025 – 2026".
#
# Deliberately **not** folded into _PDF_RANGE. A *range* of years anchors
# anywhere in the document, because "2018 - 2021" after a degree says what a
# month range says. A bare single year anchors only inside a PROJECTS section.
# It is a weak anchor: the PROFILE paragraph on the résumé this was built
# against ends "(B.S., Computer Science, May 2020)", and a document-wide
# single-year scan reads that as an engagement and invents one out of the
# summary. Bounding the bare year to the section where undated entries actually
# live is what makes it safe.
#
# The range alternative is greedy and comes first, so "2025 – 2026" is one
# anchor rather than two.
_PDF_YEAR = re.compile(
    r"(?<![\d.,/-])((?:19|20)\d{2})"
    r"(?:\s*(?:-|to|through|–|—)\s*((?:19|20)\d{2}|present|current))?"
    r"(?![\d.,/%-])",
    re.I,
)

# Section headings, used both to bound the skills section and to stop a label
# from reaching back into the entry above it.
_SECTIONS = (
    r"EDUCATION|EXPERIENCE|EMPLOYMENT|PROJECTS?|SKILLS?|AWARDS|"
    r"CERTIFICATIONS?|PUBLICATIONS|INTERESTS|REFERENCES|SUMMARY|OBJECTIVE"
)
_SKILLS_HEAD = re.compile(r"\bSKILLS?\b")
# The headings that say what an entry under them is, keyed with any trailing S
# dropped so PROJECT and PROJECTS are one key. Every other heading says nothing.
_SECTION_KIND = {"EXPERIENCE": "work", "EMPLOYMENT": "work",
                 "EDUCATION": "education", "PROJECT": "project"}

# The layout-gap marker. See parse_engagements: a run of two or more spaces in
# extracted PDF text is a column or field break, and it is the only boundary
# between one entry's last bullet and the next entry's label. EN SPACE is used
# rather than a private sentinel precisely because it is real whitespace, so
# `\s` and `str.split()` handle it without anything else being told about it.
_GAP = " "

# A finished sentence, used both by the label boundary below and by the bullet
# splitter's fallback. One definition shared, because the first draft had two
# copies and only one of them learned that a sentence can end in an acronym: a
# period after a letter, digit or bracket, unless that letter is a lone capital,
# which is an initial ("B.S. Capstone") and not the end of a word.
_SENTENCE_END = r"(?<![^A-Za-z][A-Z])(?<=[A-Za-z0-9)%])\.\s"

# Anything a label cannot span: a heading, a bullet, a finished sentence, a line
# break, or a layout gap.
#
# The sentence alternative reads: a period after a letter, digit or bracket,
# **unless** the letter is a lone capital. Both halves are load bearing. Without
# the digit and the capital it missed "…a Raspberry Pi at 18 FPS. Tidepool —
# Group Project", because the sentence ends in an acronym, and the next entry's
# whole label came back with a bullet stuck to the front of it. Without the
# exclusion it cuts inside "B.S. Capstone" and "Ph.D. Physics", where the period
# follows an initial and the label continues; the lookbehind is two characters
# wide, so it fires only when the capital is itself preceded by a non-letter.
_LABEL_STOP = re.compile(
    r"\b(?:" + _SECTIONS + r")\b|[•|\n" + _GAP + r"]|" + _SENTENCE_END
)

# Contact-block patterns. All three are also used as scrubbers on anything
# headed for knowledge/, which renders publicly and is fed to retrieval.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+\w")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
)
# A street address: a house number, capitalised words, a street type, and an
# optional unit. Scrubbed for the same reason as a phone number. A résumé that
# prints one in its contact line was the only leak the scored fakes found.
_STREET = re.compile(
    r"\b\d{2,6}\s+(?:[A-Z][\w'.-]*\s+){1,4}"
    r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct)\b\.?"
    r"(?:,?\s*(?:(?:Apt|Suite|Floor|Unit)\.?\s*|#\s*)[\w-]+)?"
)

# City plus a US state, either two-letter or spelled out. Deliberately narrow:
# a loose "Word, Word" matches half the bullets on a résumé.
#
# The city is **one** word unless it opens with a known multi-word prefix, which
# is not fussiness. A greedy run of capitalised words read a real header shaped
# like "University of Somewhere Augusta, GA" as the location "Somewhere Augusta,
# GA", because nothing in a run of capitals says where a school's name ends and
# a city begins. The prefix list is what distinguishes "New York" from that.
_LOCATION = re.compile(
    r"\b((?:New|San|Los|Las|St\.?|Saint|Fort|Ft\.?|North|South|East|West|Port|"
    r"Lake|Mount|Mt\.?|Palm|Santa|El|Ann|Cape|Grand|Long|Salt Lake) )?"
    r"([A-Z][a-zA-Z.'-]+),\s*"
    r"(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYCD]|"
    r"O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY]|Alabama|Alaska|Arizona|Arkansas|"
    r"California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|"
    r"Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|"
    r"Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|"
    r"Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|"
    r"North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|"
    r"South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|"
    r"Washington|West Virginia|Wisconsin|Wyoming)\b"
)
# The profile links a forker actually has. Matched without a scheme because a
# résumé writes "linkedin.com/in/name" as often as it writes the full URL.
_LINK_HOSTS = (
    ("LinkedIn", re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w%-]+", re.I)),
    ("GitHub", re.compile(r"(?:https?://)?(?:www\.)?github\.com/[\w.-]+", re.I)),
    ("Scholar", re.compile(r"(?:https?://)?scholar\.google\.com/\S+", re.I)),
)

# The name at the very start of the document: two to four capitalised words,
# with an optional middle initial and an optional generational suffix.
#
# A *prefix* match, not a whole-line one, because the line it sits on carries
# the role and the whole contact block behind it. `[A-Z][a-z]` rather than
# `[A-Z]\w` is what stops it: a real header reads like "Dana Reyes B.S.
# Computer Science", and requiring a lowercase letter after each capital ends
# the name at "B.S." instead of swallowing it.
_NAME_AT_START = re.compile(
    r"^\s*([A-Z][a-z'’-]+(?: [A-Z]\.?)?(?: [A-Z][a-z'’-]+){1,3}"
    r"(?: (?:Jr|Sr|II|III|IV)\.?)?)\b"
)

# Where a name stops and a job title starts, when the two sit on one line with
# nothing between them. The run above is greedy and cannot tell: "Ada P. Okonkwo
# Machine Learning Engineer" came back as the name "Ada P. Okonkwo Machine" and
# the role "Learning Engineer", because "Machine" is a capitalised word like any
# other. A name carries no role noun and a title always does, so the run is cut
# at the first of these instead of being guessed at by length.
_TITLE_WORD = re.compile(
    r"\b(Machine|Data|Software|Senior|Junior|Principal|Staff|Lead|Chief|Head|"
    r"Full|Stack|Front|Back|Product|Project|Program|Engineer|Engineering|"
    r"Developer|Scientist|Analyst|Manager|Designer|Architect|Consultant|"
    r"Researcher|Research|Intern|Director|Specialist|Administrator|Technician|"
    r"Student|Graduate|Candidate|Professional|Marketing|Sales|Finance|"
    r"Operations|Applied|Computer|Cloud|Security|Quality|Technical)\b"
)

# Where one bullet ends and the next begins. A glyph, a dash used as one, **or a
# line break**.
#
# The line break is not redundant. PDF producers disagree completely here: pypdf
# extracted the owner's own résumé as one line with `•` glyphs and no newlines at
# all, while a résumé printed to PDF from HTML by Chrome comes out with a newline
# per list item and **no glyph at all**, because a CSS list marker is not text.
# Splitting on only one of the two means every bullet on half of all résumés is
# silently merged into its neighbour.
_BULLET_SPLIT = re.compile(r"\s*[•▪◦‣]\s*|\s+[-–—]\s+|\n+")


# A section heading appearing mid-run. Uppercase only and no IGNORECASE: these
# are the words a résumé sets as headings, and matching them case insensitively
# would cut a bullet at the word "projects".
_SECTION_HEAD = re.compile(r"\b(" + _SECTIONS.upper() + r")\b")


# One date or date range and nothing else on the line: "Mar 2023 – Present",
# "03/2021 - present", "2018-2021", "2022".
_DATE_WORD = r"(?:(?:[A-Za-z]{3,9}\.?\s+)?(?:19|20)\d{2}|\d{1,2}/(?:19|20)\d{2}|present|current)"
_DATE_ONLY_LINE = re.compile(rf"^\s*{_DATE_WORD}(?:\s*[-–—]\s*{_DATE_WORD})?\s*$", re.I)
_HEADING_LINE = re.compile(rf"^\s*({_SECTIONS})\s*$", re.I)
# The same heading glued onto the end of the bullet above it, which is how
# Google Docs extracts a heading that follows a list: "…two conference posters.
# Education". Only after a finished sentence, only capitalised, and only as the
# last word on the line, so prose that mentions a section is left alone.
_GLUED_HEADING = re.compile(rf"(?<=[a-z0-9)%]\.) ([A-Z][a-z]*(?: [A-Z][a-z]*)?)\s*$")


def _dates_hoisted(text: str) -> bool:
    """True when the extraction holds a run of three lines that are only dates.

    Chrome paints floated elements before inline content, so a résumé that
    right-aligns its dates with `float: right` comes out of pypdf with every
    date in a column ahead of the text it belongs to: at the very top on a
    single-column page, after the sidebar on a two-column one. The dates still
    parse; the labels, bullets, name and role in front of them are lost. Three
    date-only lines in a row is not something a résumé says on purpose.
    """
    run = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        run = run + 1 if _DATE_ONLY_LINE.match(line) else 0
        if run >= 3:
            return True
    return False


def normalize_extraction(text: str) -> str:
    """Undo the two producer habits that defeat the parsers, and nothing else.

    Google Docs separates *every* word with two spaces. Two or more spaces are
    otherwise the only column boundary the parsers get (_GAP), so on a Docs PDF
    each label was cut to nothing. When doubles are the normal separator they
    are collapsed; three or more still survive as a gap. A PDF where doubles
    are rare, like the one this parser was built on, is left alone, because
    there a double space really is a boundary.

    Docs also keeps headings in the case they were typed. _SECTION_HEAD is
    upper case on purpose (a sentence saying "my experience" is not a
    heading), so a line that is a section name and nothing else is upper-cased,
    and so is one glued onto the end of the previous bullet (_GLUED_HEADING).
    """
    single = len(re.findall(r"(?<=\S) (?=\S)", text))
    double = len(re.findall(r"(?<=\S) {2}(?=\S)", text))
    if double > single:
        text = re.sub(r"(?<=\S) {2}(?=\S)", " ", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
    lines = []
    for line in text.splitlines():
        if _HEADING_LINE.match(line):
            line = line.upper()
        else:
            glued = _GLUED_HEADING.search(line)
            if glued and _HEADING_LINE.match(glued.group(1)):
                # Split onto its own line as well as upper-cased, so the
                # heading also bounds the bullet it was glued to.
                line = line[: glued.start()] + "\n" + glued.group(1).upper()
        lines.append(line)
    return "\n".join(lines)


_TRAILING_DATE = re.compile(rf"^(?P<text>.*?\S)\s{{3,}}(?P<date>{_DATE_WORD}(?:\s*[-–—]\s*{_DATE_WORD})?)\s*$", re.I)


def _gutter(lines: list[str]) -> tuple[int, int] | None:
    """The run of columns that is blank on nearly every line of a two-column page.

    Returns (start, end) of the run, or None for a single-column page. Text to
    the right of the run on most lines is what separates a real gutter from the
    gap in front of a right-aligned date, which only the label lines have.
    """
    lines = [line for line in lines if line.strip()]
    if len(lines) < 8:
        return None
    width = max(len(line) for line in lines)
    # Judged only on the lines that reach past the column. Counting short lines
    # as clear found a "gutter" two characters wide in the middle of a
    # single-column page, where most lines simply ended before it.
    clear = []
    for c in range(width):
        reach = [line for line in lines if len(line.rstrip()) > c + 1]
        clear.append(len(reach) >= 4 and sum(line[c] == " " for line in reach) / len(reach) >= 0.85)
    best = None
    c = 8
    while c < width - 8:
        if not clear[c]:
            c += 1
            continue
        end = c
        while end + 1 < width and clear[end + 1]:
            end += 1
        right = sum(1 for line in lines if line[end + 1:].strip()) / len(lines)
        left = sum(1 for line in lines if line[:c].strip()) / len(lines)
        if end - c >= 1 and right >= 0.4 and left >= 0.4 and (best is None or right > best[0]):
            best = (right, c, end)
        c = end + 1
    return (best[1], best[2]) if best else None


def _lift_wrapped_dates(lines: list[str]) -> list[str]:
    """Put a date that wrapped onto a bullet line back on its label's line.

    A floated date that does not fit beside its label drops to the next line,
    which is the first bullet. Bullets are indented and labels are not, so the
    date goes to the nearest line above that is less indented than it.
    """
    out = list(lines)
    for i, line in enumerate(out):
        m = _TRAILING_DATE.match(line)
        if not m:
            continue
        indent = len(line) - len(line.lstrip())
        for j in range(i - 1, -1, -1):
            above = out[j]
            if above.strip() and len(above) - len(above.lstrip()) < indent:
                if not _TRAILING_DATE.match(above):
                    out[j] = f"{above.rstrip()}   {m['date']}"
                    out[i] = m["text"]
                break
    return out


def split_columns(page: str) -> str:
    """Re-order one page of layout-mode text into reading order.

    Layout mode fixes the order Chrome scrambled but prints a two-column page
    side by side, one visual row per line. Each column is emitted whole, left
    then right, with any full-width line (a header) kept in place between.
    Indentation is used to re-seat wrapped dates and then dropped, because the
    parsers read leading runs of spaces as layout gaps.
    """
    lines = page.splitlines()
    gutter = _gutter(lines)
    blocks: list[list[str]] = []
    if gutter is None:
        blocks.append(lines)
    else:
        # Only the right edge of the run is trusted. The 80% threshold lets a
        # long left-column line reach into the run's left side, and treating
        # that as a full-width line tipped half a column out of order.
        _, end = gutter
        start = end - 1
        left: list[str] = []
        right: list[str] = []
        for line in lines:
            if line[start:end + 1].strip():
                blocks += [left, right, [line]]
                left, right = [], []
            else:
                left.append(line[:start])
                right.append(line[end + 1:])
        blocks += [left, right]
    out = []
    for block in blocks:
        for line in _lift_wrapped_dates(block):
            # The alignment gap in front of a right-aligned date is a _GAP
            # boundary to the parsers, which then read an empty label before
            # the date. "Label Mar 2018 – Present" is the shape they expect.
            m = _TRAILING_DATE.match(line)
            line = f"{m['text'].strip()} {m['date']}" if m else line.strip()
            if line:
                out.append(line)
    return "\n".join(out)


def read_pdf_text(path: Path) -> str | None:
    """Extract the text of a PDF, or explain why it could not be read.

    Returns None rather than raising on every failure, because an import that
    cannot run is a reason to fall back to typing, not to lose the whole setup
    session partway through.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        print("\n  Reading a PDF needs pypdf, which is not installed:")
        print("      python3 -m pip install pypdf")
        print("  It is not a dependency of the site, only of this script, so it")
        print("  is deliberately absent from requirements.txt.")
        return None
    try:
        reader = PdfReader(str(path))
        text = "".join((page.extract_text() or "") for page in reader.pages)
        if _dates_hoisted(text):
            # Stream order put every date ahead of the name, so read the page by
            # position instead. Only then: the default extraction is what the
            # parsers below were tuned against, and it is right for every other
            # producer measured so far.
            text = "\n".join(split_columns(page.extract_text(extraction_mode="layout") or "")
                             for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - any malformed PDF lands here
        print(f"  Could not read {path.name}: {exc}")
        return None
    # NFKC, which splits the typographic ligatures a PDF font supplies as single
    # code points. Without it a real Chrome-printed résumé yielded "Airﬂow" and
    # "the ﬁrst time" (U+FB02, U+FB01), and those go into knowledge/resume.md, so
    # they end up on the page and in what the chatbot says. It also folds NBSP
    # and the other exotic spaces into ordinary ones.
    text = unicodedata.normalize("NFKC", text)
    text = normalize_extraction(text)
    if len(text.strip()) < 200:
        # A scanned or image-only résumé extracts to nothing. Saying so beats
        # proposing an empty skill list and letting it read as "found nothing".
        print(f"  {path.name} has almost no extractable text ({len(text.strip())} chars).")
        print("  It is probably a scan or an image export, which this cannot read.")
        return None
    return text


def read_pdf_links(path: Path) -> list[dict]:
    """Recover the profile links a résumé hyperlinks rather than spells out.

    Most résumés write the words "LinkedIn" and "GitHub" and hang the URL off
    them as a link annotation, so the extracted *text* contains no URL at all
    and parse_contact finds nothing to offer. The annotation is where the
    address actually lives.

    Returns [] on any failure. A PDF with no annotations is the ordinary case,
    not an error, and this runs after the text has already been read.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
    except Exception:  # noqa: BLE001 - pypdf absent, or an unreadable file
        return []

    seen: dict[str, str] = {}
    for page in reader.pages:
        try:
            annots = page.get("/Annots") or []
        except Exception:  # noqa: BLE001 - a malformed annotation array
            continue
        for annot in annots:
            try:
                uri = (annot.get_object().get("/A") or {}).get("/URI")
            except Exception:  # noqa: BLE001 - a broken indirect reference
                continue
            if not uri:
                continue
            for label, pattern in _LINK_HOSTS:
                # First one wins. A résumé links its GitHub profile once in the
                # header and then links four repositories under the projects,
                # and the header one is the profile link.
                if pattern.search(str(uri)) and label not in seen:
                    seen[label] = str(uri).rstrip("/.,;")
    return [{"label": label, "url": url} for label, url in seen.items()]


def scrub_private(text: str) -> str:
    """Remove the contact details that must not reach knowledge/.

    Everything this script writes into `knowledge/` is rendered on the public
    page and indexed for retrieval, so a phone number pasted out of a résumé is
    published *and* becomes something the chatbot will read out on request.
    The same already applies to the PDF itself, which is why
    `tools/strip_pdf_phone.py` exists; this is the text-side equivalent.

    Emails go too. The one email on the page is the one answered at the Identity
    prompt, which the forker chose to make public; a second one recovered from a
    résumé header was never offered that choice.
    """
    text = _PHONE.sub("", text)
    text = _EMAIL.sub("", text)
    text = _STREET.sub("", text)
    # Collapse whatever the removals left behind: a doubled separator, or a
    # space before punctuation.
    text = re.sub(r"\s*[|·•]\s*(?=[|·•])", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" |·•,;-")


def parse_contact(text: str) -> dict:
    """Read the header block for the six Identity answers a résumé already has.

    Only the top of the document is searched. A résumé mentions cities and
    capitalised names all the way down, so widening this turns a confident
    answer into a plausible wrong one, and a wrong pre-filled default is worse
    than an empty prompt: it is the answer a forker presses Enter past.

    Everything here is a *proposal* that the ordinary prompts show as a default.
    Nothing is written from this function.
    """
    # Newlines are kept, spaces are not. Whether there *are* any newlines is up
    # to the PDF producer: pypdf extracted the owner's own résumé as a single
    # line, where a Chrome print of the same content has one per field. So the
    # name and the role are separated by a newline on one résumé and by a `|` on
    # another, and both have to work. NBSP is normalised because it is a PDF
    # artifact that `\s` handles but `str.split` on a literal space does not.
    flat = re.sub(r"[^\S\n]+", " ", text.replace("\xa0", " "))
    # The header ends at the first section heading, and 600 characters is the
    # backstop for a résumé that has none. Searching the whole document instead
    # turns a confident answer into a plausible wrong one: cities and
    # capitalised names appear all the way down, and a wrong pre-filled default
    # is worse than an empty prompt because it is the one a forker presses
    # Enter past.
    head_stop = _SECTION_HEAD.search(flat)
    head = flat[: min(head_stop.start() if head_stop else len(flat), 600)]

    found: dict = {}

    email = _EMAIL.search(head)
    if email:
        found["email"] = email.group(0)

    location = _LOCATION.search(head)
    if location:
        city = (location.group(1) or "") + location.group(2)
        found["location"] = f"{city}, {location.group(3)}"

    links = []
    for label, pattern in _LINK_HOSTS:
        hit = pattern.search(head)
        if hit:
            url = hit.group(0).rstrip("/.,;")
            if not url.lower().startswith("http"):
                url = "https://" + url
            links.append({"label": label, "url": url})
    if links:
        found["links"] = links

    name = _NAME_AT_START.match(head)
    if name:
        run = name.group(1)
        # Cut the greedy run where the job title starts. Never to nothing: a
        # person called "Grace Computer" keeps their name and loses only the
        # role, which is a prompt they answer, rather than losing both.
        title = _TITLE_WORD.search(run)
        if title and len(run[: title.start()].split()) >= 2:
            run = run[: title.start()].strip()
        found["name"] = run
        # The role is whatever sits between the name and the first separator,
        # which is where every résumé this was tested against puts it.
        rest = head[name.start(1) + len(run):]
        # The first non-empty segment, not the first. `rest` begins at the
        # separator itself, so on a newline-delimited résumé segment zero is the
        # empty string and taking it lost the role entirely.
        segments = [s.strip(" ,;-–—") for s in re.split(r"[|•·•\n]", rest)]
        candidate = next((s for s in segments if s), "")
        # Cut where the location begins. On a résumé that puts the role and the
        # contact line on one line with only a space between them, the segment
        # read "Data Scientist Chicago, IL, 4821 Lakewood Avenue, Apt 6B", and
        # the role is the one header field rendered into knowledge/.
        if location and location.group(0) in candidate:
            candidate = candidate[: candidate.index(location.group(0))].strip(" ,;-–—")
        if 3 < len(candidate) <= 70 and not _EMAIL.search(candidate):
            found["role"] = candidate

    # A summary or objective paragraph is the closest thing a résumé carries to
    # a tagline, and it is the author's own copy rather than anything generated.
    # Matched in caps and without IGNORECASE, because these are heading words
    # and the lowercase forms are ordinary prose: "profile" appears in a bullet.
    summary = re.search(r"\b(?:PROFESSIONAL )?(?:SUMMARY|OBJECTIVE|PROFILE|ABOUT)\b:?", flat)
    if summary:
        rest = flat[summary.end():]
        stop = _SECTION_HEAD.search(rest)
        para = scrub_private(" ".join(rest[: stop.start() if stop else 700].split()))
        if 40 <= len(para) <= 700:
            found["tagline"] = para

    return found


def extract_bullets(span: str, next_label: str = "") -> list[str]:
    """Pull an engagement's own bullets out of its span of résumé text.

    Split on the bullet glyphs rather than on lines. pypdf extracted the whole
    of the résumé this was built against as a single line, so a line-based
    reader finds one bullet per *engagement*, and the glyphs are the only
    structure that actually survives extraction.

    Two things are trimmed, and both were real:

    * Everything from a section heading onwards. A span runs to the next
      *date range*, and a PROJECTS section whose entries are dated by bare year
      matches no range, so the whole of it landed inside the last dated job and
      the corpus claimed three projects as that employer's work.
    * The next engagement's label, which sits in the tail of this span by
      construction: parse_engagements reads a label backwards out of the text
      in front of its date.

    The fallback splits on sentences, for a résumé or an extractor that loses
    the glyphs entirely. It keeps only segments long enough to be a claim.
    """
    # Whether anything sat on the date's own line after it. Checked before the
    # strip below throws the leading newline away.
    header_on_date_line = not span.lstrip(" \t").startswith("\n")
    # Horizontal whitespace only. `" ".join(span.split())` was here and it threw
    # away the newlines that _BULLET_SPLIT needs, so on a producer that delimits
    # bullets by line and emits no glyph, every bullet of an entry came back as
    # one blob with the entry's location glued to the front of it.
    flat = re.sub(r"[^\S\n]+", " ", span).strip()
    heading = _SECTION_HEAD.search(flat)
    if heading:
        flat = flat[: heading.start()]
    if next_label:
        cut = flat.rfind(next_label)
        if cut > 0:
            flat = flat[:cut]

    parts = [p for p in _BULLET_SPLIT.split(flat) if p and p.strip()]
    # Rejoin a wrapped bullet. A line break is a bullet boundary in one producer
    # and a mid-sentence wrap in another, and the only thing distinguishing them
    # is that a continuation starts lowercase where a new bullet starts with a
    # capital. Without this, every bullet long enough to wrap is cut in half and
    # both halves land in the corpus as separate claims.
    joined: list[str] = []
    for part in parts:
        if joined and part[:1].islower():
            joined[-1] = f"{joined[-1]} {part}".strip()
        else:
            joined.append(part)
    parts = joined
    # The text in front of the first bullet is the entry's own header: the
    # location, a GPA, a tech list. It is not a bullet, and dropping it is only
    # safe when there *was* a boundary for it to sit in front of, and when it
    # sat on the date's own line. Where the date ends its line the first part
    # is the first bullet, and dropping it lost one claim per engagement on
    # every newline-delimited résumé without bullet glyphs.
    if len(parts) > 1:
        if header_on_date_line:
            parts = parts[1:]
    else:
        parts = re.split(_SENTENCE_END, flat)

    cleaned = []
    for item in parts:
        # Scrub each sentence, then drop the ones left with nothing in them.
        # Removing only the digits leaves "Reach me at." on the page and in the
        # chatbot's mouth, but dropping every sentence that *contained* a number
        # threw away "Shipped a service handling 40,000 requests a day, call
        # 678-555-0143 for details", which is a real achievement with a phone
        # number in the middle of it. Scrubbing first tells the two apart: a
        # sentence whose whole point was the number has almost nothing left.
        kept = []
        for sentence in re.split(_SENTENCE_END, item):
            sentence = scrub_private(sentence).strip(" .,;")
            if len(sentence) >= 20:
                kept.append(sentence)
        item = ". ".join(kept).rstrip(" .")
        # Long enough to be a claim, short enough not to be a whole section.
        if 25 <= len(item) <= 400:
            cleaned.append(item + ".")
    return cleaned[:8]


def canonical_skill(name: str) -> str:
    """Return the catalogue's spelling of a skill when it knows one.

    A résumé writes 'data visualization' and the catalogue writes 'Data
    Visualization'. Left alone the two are different skills, so the network
    draws two nodes and report_duplicate_skills has nothing to say about it
    because they are not literally equal. Matching is case insensitive and
    anything unknown is returned untouched, since free text is a supported
    state everywhere else in this script.
    """
    name = " ".join(name.split()).strip(" .,;:")
    for group_skills in SKILL_CATALOG.values():
        for known in group_skills:
            if known.casefold() == name.casefold():
                return known
    return name


def parse_skill_section(text: str) -> dict[str, list[str]]:
    """Pull the résumé's own skills section, keeping the author's own grouping.

    This is the highest fidelity source available and it beats scanning the
    whole document against SKILL_CATALOG, which was measured on a real résumé to
    find fewer skills and to miss every one the catalogue does not carry
    (MediaPipe, PySpark, TCNs). More importantly it recovers the *groups* the
    person already chose, which is the one part of this job that would otherwise
    need a model to guess at.

    Returns {} when there is no skills section, which is a normal résumé and the
    caller's cue to fall back to the catalogue scan.
    """
    upper = text.upper()
    start = upper.find("SKILL")
    if start < 0:
        return {}
    # Stop at the next section heading so an unlabelled trailing paragraph is
    # not swallowed into the last group.
    rest = text[start:]
    stops = [
        m.start()
        for m in re.finditer(
            r"\b(EDUCATION|EXPERIENCE|EMPLOYMENT|PROJECTS?|AWARDS|"
            r"CERTIFICATIONS?|PUBLICATIONS|INTERESTS|REFERENCES|SUMMARY|OBJECTIVE)\b",
            rest[1:],
        )
    ]
    section = rest[: stops[0] + 1] if stops else rest[:1600]

    groups: dict[str, list[str]] = {}
    # "Label: a, b, c", bulleted or not. The label is bounded to keep a prose
    # sentence containing a colon from being read as a group name.
    for match in re.finditer(r"[•\-\*\n]\s*([A-Za-z][A-Za-z /&+#]{2,40}?)\s*:\s*([^•\n]+)", section):
        label = " ".join(match.group(1).split())
        members = [
            canonical_skill(part)
            for part in re.split(r"[,;]", match.group(2))
            if part.strip()
        ]
        # One character is a skill: R and C are languages. `> 1` dropped R.
        members = [m for m in members if 0 < len(m) <= 40]
        if label and members:
            groups.setdefault(label, [])
            for member in members:
                if member not in groups[label]:
                    groups[label].append(member)
    if groups:
        return groups

    # A group name with no colon: the name alone on its line and the members on
    # the next, which is how Chrome and Docs extract a résumé that sets the name
    # as a subheading. Only tried when no "Label:" group was found, and only a
    # short line with no punctuation counts as a name, so a wrapped member list
    # is not read as one.
    lines = [line.strip() for line in section.splitlines()[1:]]
    for name, members_line in zip(lines, lines[1:]):
        if not re.fullmatch(r"[A-Z][A-Za-z /&+#]{1,40}", name) or "," not in members_line:
            continue
        members = [canonical_skill(part) for part in re.split(r"[,;]", members_line) if part.strip()]
        members = [m for m in members if 0 < len(m) <= 40]
        if members:
            groups.setdefault(name, [])
            for member in members:
                if member not in groups[name]:
                    groups[name].append(member)
    return groups


def scan_catalog(text: str) -> dict[str, list[str]]:
    """Fallback: find catalogue skills anywhere in the document.

    Only used when the résumé has no skills section of its own. Weaker by
    construction, since it can only find the ~70 things the catalogue knows.

    The boundaries are lookarounds rather than \\b, which cannot match a skill
    ending in punctuation: in `C++,` the \\b after `+` needs a word character
    and finds a comma, so `\\bC\\+\\+\\b` silently never fires. That bug is easy
    to write and impossible to see in the output, which is only ever a slightly
    short list.
    """
    found: dict[str, list[str]] = {}
    for group, members in SKILL_CATALOG.items():
        for skill in members:
            lead = r"(?<!\w)" if skill[0].isalnum() else r"(?<!\S)"
            if re.search(lead + re.escape(skill) + r"(?!\w)", text, re.I):
                found.setdefault(group, []).append(skill)
    return found


def _this_month() -> str:
    today = _dt.date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _month_anchors(flat: str) -> list[dict]:
    """Month-dated engagements: the primary anchor, and the reliable one."""
    anchors: list[dict] = []
    for match in _PDF_RANGE.finditer(flat):
        start = f"{int(match.group(2)):04d}-{_MONTHS[match.group(1)[:3].lower()]:02d}"
        tail = match.group(3).lower()
        if tail.startswith(("present", "current")):
            # The current month, not the string "present". prompt_month rejects
            # anything that is not YYYY-MM and loops until it gets one, so an
            # entry carrying "present" as its default would trap anyone pressing
            # Enter through the career prompts in a loop they cannot answer.
            # profile.json already represents ongoing roles this way.
            end = _this_month()
        else:
            end_match = re.match(r"([a-z]{3})[a-z]*\.?\s*(\d{4})", tail)
            end = (
                f"{int(end_match.group(2)):04d}-{_MONTHS[end_match.group(1)]:02d}"
                if end_match
                else start
            )
        anchors.append({"at": match.start(), "to": match.end(),
                        "start": start, "end": end, "kind": None})
    return anchors


def _numeric_anchors(flat: str, taken: list[dict]) -> list[dict]:
    """Engagements dated "MM/YYYY", which _PDF_RANGE cannot see.

    Applied to the whole document, unlike a bare year: "03/2021 - 02/2023" is
    as unambiguous as "Mar 2021 – Feb 2023", only spelled differently.
    """
    spoken = [(a["at"], a["to"]) for a in taken]
    anchors: list[dict] = []
    for match in _PDF_NUMERIC.finditer(flat):
        if any(at < match.end() and match.start() < to for at, to in spoken):
            continue
        start = f"{int(match.group(2)):04d}-{int(match.group(1)):02d}"
        if match.group(3).lower().startswith(("present", "current")):
            end = _this_month()
        else:
            end = f"{int(match.group(5)):04d}-{int(match.group(4)):02d}"
        anchors.append({"at": match.start(), "to": match.end(),
                        "start": start, "end": end, "kind": None})
    return anchors


def _year_anchors(flat: str, taken: list[dict]) -> list[dict]:
    """Year-dated engagements, which the month anchors cannot see at all.

    A year *range* anchors anywhere: "2018 - 2021" after a degree is as much a
    span as "Aug 2018 – May 2021". A bare single year anchors only inside a
    PROJECTS section, for the reason on _PDF_YEAR. Anything overlapping an
    anchor already taken is skipped, so an entry that *does* carry months is
    found once, by the better parser.

    Inside PROJECTS `kind` is forced rather than guessed. These came out of the
    section that says what they are, which beats the keyword vote on the label:
    it read "Nightjar — Bird Call Classifier (Solo)" as work.
    """
    head = re.search(r"\bPROJECTS?\b", flat)
    if head:
        tail = _SECTION_HEAD.search(flat, head.end())
        projects = (head.end(), tail.start() if tail else len(flat))
    else:
        projects = (len(flat), len(flat))
    spoken = [(a["at"], a["to"]) for a in taken]

    anchors: list[dict] = []
    for match in _PDF_YEAR.finditer(flat):
        if any(at < match.end() and match.start() < to for at, to in spoken):
            continue
        in_projects = projects[0] <= match.start() < projects[1]
        if not (in_projects or match.group(2)):
            continue
        first, second = match.group(1), (match.group(2) or "").lower()
        start = f"{int(first):04d}-01"
        if second.startswith(("present", "current")):
            end = _this_month()
        else:
            # December of the closing year, except when that year is the current
            # one: an arc segment running to a December still in the future
            # draws the engagement as longer than it is. A year genuinely in the
            # future keeps its December rather than being clamped below its own
            # start, which prompt_month would then refuse.
            last = int(second) if second else int(first)
            end = _this_month() if last == _dt.date.today().year else f"{last:04d}-12"
        anchors.append({"at": match.start(), "to": match.end(),
                        "start": start, "end": end,
                        "kind": "project" if in_projects else None})
    return anchors


def parse_engagements(text: str, known: list[str]) -> list[dict]:
    """Propose career entries, anchored on date ranges rather than headings.

    Every résumé formats headings differently and most format dates the same, so
    the range is the anchor and the label is read from the line in front of it.
    Skills are attributed by scanning the span of text belonging to each
    engagement, which is what produces the skill-to-engagement chords without
    asking anyone to fill in a matrix by hand.

    Returns proposals, not entries. `kind` in particular is guessed from
    surrounding words and is wrong often enough that the caller must confirm it.
    """
    # A run of two or more spaces is a *layout gap*, and pypdf preserves it.
    # Collapsing it, which this used to do first thing, threw away the only
    # boundary in the document between the end of one entry's last bullet and
    # the start of the next entry's label: a real résumé reads "…Robotics Team,
    # Chess Club  Riverside High School — Dual Enrollment", two spaces, and the
    # label came back as "Chess Club Riverside High School". A club glued to a
    # school is the kind of wrong a forker accepts by mistake.
    #
    # EN SPACE and not a private sentinel, because it has to *be* whitespace
    # everywhere else: `\s` matches it, `str.split()` splits on it, so
    # _PDF_RANGE and extract_bullets need no knowledge of it at all.
    flat = re.sub(r"[ \t]{2,}", _GAP, text.replace("\xa0", " "))
    flat = flat.replace("\t", " ")
    # Month names first, then numeric months, then years, each skipping what a
    # better parser already took, then everything in document order because
    # the span of each anchor runs to the next one.
    anchors = _month_anchors(flat)
    anchors += _numeric_anchors(flat, anchors)
    anchors += _year_anchors(flat, anchors)
    anchors.sort(key=lambda a: a["at"])
    # A skills section belongs to no engagement. Left in, whichever entry sits
    # last in the document swallows it and comes back claiming every skill on
    # the résumé, which looks like a working parser and is not.
    skills_at = _SKILLS_HEAD.search(flat)
    skills_at = skills_at.start() if skills_at else len(flat)
    out: list[dict] = []
    spans: list[str] = []
    for i, anchor in enumerate(anchors):
        start, end = anchor["start"], anchor["end"]

        # The label sits immediately in front of the date, so this reads the
        # *end* of the preceding window. Splitting the window into lines and
        # taking the first one was the earlier version and it returned the tail
        # of the previous entry's prose, because pypdf extracts these runs with
        # no newline in them at all: the split produced a single segment and
        # "first line" meant "160 characters ago".
        # Read uncollapsed, and collapsed only *after* the boundary search. The
        # whitespace is the signal here, so normalising it first is what lost
        # the boundary; that is the whole of the fraternity-glued-to-a-school bug.
        before = flat[max(0, anchor["at"] - 200) : anchor["at"]]
        # Cut at the nearest thing that cannot be part of a label: a section
        # heading, a bullet, the end of a sentence, or a layout gap.
        cut = 0
        for boundary in _LABEL_STOP.finditer(before):
            cut = boundary.end()
        label = " ".join(before[cut:].split()).strip(" -–—,;:")
        if len(label) > 92:
            # Trim from the left at a word boundary. A blind slice produced
            # "aretta High School", which reads like a typo rather than a
            # truncation and is the kind of thing a forker accepts by mistake.
            label = re.sub(r"^\S*\s+", "", label[-92:])

        end_of_span = anchors[i + 1]["at"] if i + 1 < len(anchors) else len(flat)
        if anchor["to"] < skills_at < end_of_span:
            end_of_span = skills_at
        # Any section heading ends the span, not only SKILLS. An engagement runs
        # to the next *date range*, so a PROJECTS section dated by bare year
        # matches no range and the whole of it fell inside the last dated job:
        # "Data Analyst — Trailhead" came back claiming PyTorch, OpenCV and
        # Swift, which belong to the two projects below it. The skills
        # decide a chord on the arc, so a bled span is a drawn falsehood.
        heading = _SECTION_HEAD.search(flat, anchor["to"], end_of_span)
        if heading:
            end_of_span = heading.start()
        span = flat[anchor["to"] : end_of_span]

        window = (label + " " + span)
        hits = []
        for skill in known:
            lead = r"(?<!\w)" if skill[0].isalnum() else r"(?<!\S)"
            # Join the words with `\s+` rather than escaping the skill whole.
            # re.escape turns a space into `\ `, which matches one literal
            # space, so a multi-word skill stopped matching across a layout gap
            # the moment gaps stopped being collapsed. "Data Visualization" is
            # the live case.
            needle = r"\s+".join(re.escape(word) for word in skill.split())
            if re.search(lead + needle + r"(?!\w)", window, re.I):
                hits.append(skill)

        # A kind the anchor already knows beats every guess below it. Only the
        # year anchor sets one, and it sets it because the entry came out of the
        # section that says what it is: "Nightjar — Bird Call Classifier
        # (Solo)" loses the keyword vote and reads as work.
        if anchor["kind"]:
            out.append({"label": label, "start": start, "end": end,
                        "kind": anchor["kind"], "skills": hits})
            spans.append(span)
            continue

        # Then the section the anchor sits under. normalize_extraction has
        # already upper-cased a heading typed in title case, so _SECTION_HEAD
        # sees it whichever producer wrote it. Only the last heading in front of
        # the anchor counts, and only one of these three says what an entry is.
        section = None
        for section in _SECTION_HEAD.finditer(flat, 0, anchor["at"]):
            pass
        kind = _SECTION_KIND.get(section.group(1).rstrip("S")) if section else None
        if kind:
            out.append({"label": label, "start": start, "end": end,
                        "kind": kind, "skills": hits})
            spans.append(span)
            continue

        # Decide from the label, and only fall back to the surrounding prose.
        # Reading both at once made "Senior Engineer — Northwind" come back as
        # education, because the bullets under it happened to mention a
        # university. What an engagement *is* is stated in its title.
        edu = r"\b(b\.?s\.?|b\.?a\.?|m\.?s\.?|m\.?b\.?a|ph\.?d|university|college|school|academy|degree|diploma)\b"
        proj = r"\b(project|capstone|hackathon|side project|open source)\b"
        work = r"\b(engineer|developer|analyst|intern|manager|consultant|scientist|annotator|assistant|lead|founder)\b"
        low_label = label.lower()
        if re.search(edu, low_label):
            kind = "education"
        elif re.search(proj, low_label):
            kind = "project"
        elif re.search(work, low_label):
            kind = "work"
        elif re.search(edu, window.lower()):
            kind = "education"
        elif re.search(proj, window.lower()):
            kind = "project"
        else:
            kind = "work"
        out.append({"label": label, "start": start, "end": end,
                    "kind": kind, "skills": hits})
        spans.append(span)

    # Bullets in a second pass, because trimming a span needs the *next*
    # entry's label and that is not known inside the loop that builds it.
    #
    # `bullets` is not a profile key and must never reach profile.json:
    # collect_career appends a kept entry verbatim, so anything left on the dict
    # is written. import_resume pops it off in one line.
    for i, entry in enumerate(out):
        next_label = out[i + 1]["label"] if i + 1 < len(out) else ""
        entry["bullets"] = extract_bullets(spans[i], next_label)
    return out
