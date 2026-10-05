"""The résumé import's parsers, pinned against the failures they already had.

Every bug in here produced *plausible* output: a real label with the wrong words
in it, a real engagement claiming another engagement's skills, a real location
that was two place names glued together. None of them raised, and none of them
would be caught by running setup and looking at the terminal, which is how they
survived. That is the same argument `tests/test_profile_checks.py` makes about
the silent-absence class, applied one layer earlier.

No PDF is read. `read_pdf_text` returns a string and every parser takes one, so
the fixtures below are that string. They are written the way pypdf actually
emits a résumé, which is the single most important thing these tests encode:
**one line, no newlines at all**, with runs of two or more spaces where the
layout had a column or field break.
"""

import sys
import unicodedata
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import setup_profile as sp  # noqa: E402
import resume_import as ri  # noqa: E402


# One line, no newlines, double spaces at the layout breaks. Anything that
# reformats this fixture into tidy lines is testing a document pypdf never
# produces, and three of the bugs below only exist in this shape.
RESUME = (
    "Ada P. Okonkwo Machine Learning Engineer  |  "
    "University of Somewhere Augusta, GA  •  ada@example.com  •  "
    "(678) 555-0143  •  linkedin.com/in/ada-okonkwo  •  github.com/ada "
    "PROFILE Engineer with six years building applied computer vision systems "
    "and a habit of measuring what shipped. Seeking roles in ML engineering. "
    "EDUCATION University of Somewhere — B.S. Computer Science Aug 2016 – May 2020 "
    "Athens, GA  •  GPA: 3.8  • Coursework: Algorithms (A), Operating Systems (A) "
    "• Clubs: Robotics Team, Chess Club  Riverside High School — Dual Enrollment "
    "Aug 2012 – May 2016 Athens, GA  •  GPA: 4.0 "
    "EXPERIENCE Senior Engineer — Northwind Mar 2021 – Present Remote "
    "• Shipped a real-time inference service handling 40,000 requests a day. "
    "Call 678-555-0143 for details. "
    "• Cut p99 latency from 900ms to 120ms by batching at the edge. "
    "Data Analyst — Trailhead Jun 2020 – Feb 2021 Athens, GA "
    "• Built the reporting pipeline the sales team still runs on. "
    "PROJECTS Nightjar — Bird Call Classifier (Solo) 2024 – 2025 "
    "Python, PyTorch, OpenCV "
    "• Trained a CNN on 30,000 labelled clips and held out by recording site. "
    "• Deployed to a Raspberry Pi at 18 FPS. "
    "Tidepool — Group Project 2023 Swift, Django "
    "• Built the iOS client in Swift against a Django back end. "
    "SKILLS • ML / Computer Vision: PyTorch, OpenCV, CNNs, Model Eval "
    "• Languages: Python, Swift, SQL "
    "• Data & Infrastructure: PostgreSQL, REST APIs, Data Visualization, Git"
)


@pytest.fixture(scope="module")
def skills():
    return ri.parse_skill_section(RESUME)


@pytest.fixture(scope="module")
def flat_skills(skills):
    return [s for members in skills.values() for s in members]


@pytest.fixture(scope="module")
def career(flat_skills):
    return ri.parse_engagements(RESUME, flat_skills)


@pytest.fixture(scope="module")
def by_label(career):
    return {entry["label"]: entry for entry in career}


# --------------------------------------------------------------------------
# parse_contact
# --------------------------------------------------------------------------

def test_identity_is_read_from_the_header():
    found = ri.parse_contact(RESUME)
    assert found["name"] == "Ada P. Okonkwo"
    assert found["role"] == "Machine Learning Engineer"
    assert found["email"] == "ada@example.com"


def test_location_is_the_city_and_not_the_university_before_it():
    """A greedy run of capitalised words read "University of Somewhere Augusta,
    GA" as the location "Somewhere Augusta, GA"."""
    assert ri.parse_contact(RESUME)["location"] == "Augusta, GA"


def test_the_role_is_found_when_a_newline_separates_it_from_the_name():
    """Whether there are newlines at all is up to the PDF producer: pypdf gave
    the owner's résumé as one line, a Chrome print gives one per field. `rest`
    begins at the separator, so segment zero is empty and taking it lost the
    role entirely."""
    head = "Priya Raghunathan\nSenior Backend Engineer\nFremont, CA\nSUMMARY\nx"
    found = ri.parse_contact(head)
    assert found["name"] == "Priya Raghunathan"
    assert found["role"] == "Senior Backend Engineer"


def test_ligatures_are_split_so_they_do_not_reach_the_corpus():
    """A PDF font supplies "fl" and "fi" as single code points, and a real
    Chrome-printed résumé yielded "Airﬂow" and "the ﬁrst time". That text is
    written into knowledge/resume.md, so it reaches the page and the chatbot."""
    assert unicodedata.normalize("NFKC", "Airﬂow the ﬁrst") == "Airflow the first"


def test_a_multi_word_city_survives_the_same_narrowing():
    header = "Ada Okonkwo Engineer  |  New York, NY  •  ada@example.com PROFILE x"
    assert ri.parse_contact(header)["location"] == "New York, NY"


def test_links_are_recovered_without_a_scheme_and_given_one():
    urls = {link["label"]: link["url"] for link in ri.parse_contact(RESUME)["links"]}
    assert urls["LinkedIn"] == "https://linkedin.com/in/ada-okonkwo"
    assert urls["GitHub"] == "https://github.com/ada"


def test_the_tagline_comes_from_the_summary_section_and_stops_at_the_next_one():
    tagline = ri.parse_contact(RESUME)["tagline"]
    assert tagline.startswith("Engineer with six years")
    assert "EDUCATION" not in tagline
    assert "University of Somewhere —" not in tagline


def test_a_lowercase_profile_in_prose_is_not_a_heading():
    """The heading words are matched in caps with no IGNORECASE. A résumé whose
    bullets mention a "profile" must not have one read as a section."""
    text = "Ada Okonkwo Engineer  |  ada@example.com EXPERIENCE • Built a user profile page. "
    assert "tagline" not in ri.parse_contact(text)


def test_nothing_is_invented_when_the_header_carries_nothing():
    assert ri.parse_contact("EXPERIENCE • Did a thing that took a while. ") == {}


# --------------------------------------------------------------------------
# scrub_private
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "call 678-555-0143 now", "call (678) 555-0143 now",
    "call 678.555.0143 now", "call +1 678 555 0143 now",
])
def test_phone_numbers_are_stripped_in_every_shape(raw):
    assert "0143" not in ri.scrub_private(raw)


def test_a_long_number_that_is_not_a_phone_survives():
    assert "40,000" in ri.scrub_private("handling 40,000 requests a day")
    assert "120ms" in ri.scrub_private("cut p99 latency to 120ms")


def test_emails_are_stripped_from_corpus_bound_text():
    assert "@" not in ri.scrub_private("reach me at ada@example.com please")


# --------------------------------------------------------------------------
# parse_engagements: labels
# --------------------------------------------------------------------------

def test_a_label_does_not_reach_back_across_a_layout_gap(by_label):
    """The club-glued-to-a-school bug. "…Robotics Team, Chess Club" ends
    with a double space before the next entry's label, and collapsing that gap
    before searching for the boundary produced "Chess Club Riverside High
    School". The gap is the only boundary the document has here."""
    assert "Riverside High School — Dual Enrollment" in by_label
    assert not any("Chess Club" in label for label in by_label)


def test_the_bullet_the_gap_belongs_to_keeps_its_own_words(by_label):
    """The other half of the same bug: whatever the label stole was missing from
    the bullet it was taken from."""
    bullets = by_label["University of Somewhere — B.S. Computer Science"]["bullets"]
    assert any("Chess Club" in bullet for bullet in bullets)


def test_a_label_does_not_reach_back_across_a_finished_sentence(by_label):
    assert "Data Analyst — Trailhead" in by_label
    assert "Senior Engineer — Northwind" in by_label


# --------------------------------------------------------------------------
# parse_engagements: spans, skills and bullets
# --------------------------------------------------------------------------

def test_a_job_does_not_claim_the_projects_sections_skills(by_label):
    """A span runs to the next date anchor, so before spans were cut at section
    headings the last dated job swallowed the whole PROJECTS section and came
    back claiming every tool in it."""
    assert by_label["Data Analyst — Trailhead"]["skills"] == []


def test_the_project_that_used_a_tool_is_the_one_that_claims_it(by_label):
    skills = by_label["Nightjar — Bird Call Classifier (Solo)"]["skills"]
    assert "PyTorch" in skills and "OpenCV" in skills


def test_the_skills_section_is_attributed_to_no_engagement(career):
    """Left inside the last engagement's span, that entry comes back claiming
    every skill on the résumé."""
    last = career[-1]
    assert "PostgreSQL" not in last["skills"]
    assert len(last["skills"]) < 4


def test_a_multi_word_skill_matches_across_a_layout_gap():
    """re.escape turns a space into `\\ `, which matches exactly one literal
    space, so every multi-word skill stopped matching the moment layout gaps
    stopped being collapsed."""
    text = (
        "EXPERIENCE Analyst — Somewhere Jan 2020 – Feb 2021 Remote "
        "• Owned the Data  Visualization work for the quarter. "
    )
    entries = ri.parse_engagements(text, ["Data Visualization"])
    assert entries[0]["skills"] == ["Data Visualization"]


def test_bullets_are_split_on_glyphs_not_lines(by_label):
    """pypdf emits no newlines, so a line-based reader finds exactly one bullet
    per engagement rather than one per bullet."""
    bullets = by_label["Senior Engineer — Northwind"]["bullets"]
    assert len(bullets) == 2


def test_the_next_entrys_label_is_not_a_bullet_of_this_one(by_label):
    """The label sits in this span's tail by construction, because it is read
    backwards out of the text in front of the next date."""
    bullets = by_label["Senior Engineer — Northwind"]["bullets"]
    assert not any("Data Analyst" in bullet for bullet in bullets)


def test_an_entrys_own_header_is_not_a_bullet(by_label):
    """The text in front of the first glyph is the location, a GPA or a tech
    list, and it came back as short bullets at the top of every entry."""
    bullets = by_label["Nightjar — Bird Call Classifier (Solo)"]["bullets"]
    assert not any(bullet.startswith("Python, PyTorch") for bullet in bullets)


def test_bullets_are_scrubbed_of_contact_details(by_label):
    """knowledge/resume.md is rendered publicly and fed to retrieval, so a phone
    number pasted out of a résumé is both published and quotable by the bot."""
    bullets = by_label["Senior Engineer — Northwind"]["bullets"]
    assert not any("555-0143" in bullet for bullet in bullets)
    assert any("40,000 requests" in bullet for bullet in bullets)


def test_a_sentence_that_was_only_a_phone_number_is_dropped_whole(by_label):
    """Scrubbing only the digits left "Call for details." on the page and in the
    chatbot's mouth."""
    bullets = by_label["Senior Engineer — Northwind"]["bullets"]
    assert any(b.startswith("Shipped a real-time inference service") for b in bullets)
    assert not any("Call" in bullet for bullet in bullets)


def test_a_claim_with_a_number_buried_in_it_survives_the_scrub():
    """The other side of the same rule. Dropping every sentence that *contained*
    a phone number threw away a real achievement that happened to carry one."""
    text = (
        "EXPERIENCE\nAnalyst — Somewhere Jan 2020 – Feb 2021\nRemote\n"
        "• Shipped the service handling 40,000 requests a day, call 678-555-0143 for details.\n"
    )
    bullet = ri.parse_engagements(text, [])[0]["bullets"][0]
    assert "40,000 requests a day" in bullet
    assert "555-0143" not in bullet


def test_bullets_delimited_by_line_break_and_no_glyph_are_split():
    """A Chrome print of an HTML résumé emits a newline per list item and no
    bullet glyph at all, because a CSS list marker is not text. Splitting on
    glyphs alone merged every bullet of an entry into one blob, with the entry's
    own location line glued to the front of it."""
    text = (
        "EXPERIENCE\nStaff Engineer — Larkspur Jan 2022 – Present\nSan Jose, CA\n"
        "Rebuilt the ledger write path, cutting settlement lag to under 90 seconds.\n"
        "Introduced idempotency keys, eliminating a class of double-charge incidents.\n"
    )
    entry = ri.parse_engagements(text, [])[0]
    assert len(entry["bullets"]) == 2
    assert not any(b.startswith("San Jose") for b in entry["bullets"])


def test_a_wrapped_bullet_is_rejoined_rather_than_cut_in_half():
    """A line break is a bullet boundary in one producer and a mid-sentence wrap
    in another. A continuation starts lowercase; a new bullet starts capitalised."""
    text = (
        "EXPERIENCE\nAnalyst — Somewhere Jan 2020 – Feb 2021\nRemote\n"
        "Owned the reporting pipeline and the nightly reconciliation job that\n"
        "the whole sales team still runs on every single morning.\n"
    )
    entry = ri.parse_engagements(text, [])[0]
    assert len(entry["bullets"]) == 1
    assert "reconciliation job that the whole sales team" in entry["bullets"][0]


def test_bullets_never_reach_the_profile(career):
    """collect_career appends a kept entry verbatim, so a `bullets` key left on
    an imported entry is written into profile.json. import_resume pops it."""
    seed_career = [dict(entry) for entry in career]
    bullets = {entry["label"]: entry.pop("bullets") for entry in seed_career}
    assert bullets
    for entry in seed_career:
        assert set(entry) == {"label", "start", "end", "kind", "skills"}


# --------------------------------------------------------------------------
# parse_engagements: dates and kinds
# --------------------------------------------------------------------------

def test_a_project_dated_by_bare_year_is_found(by_label):
    """_PDF_RANGE needs a month name, so a PROJECTS section dated "2024 – 2025"
    matched no anchor and every project was missed."""
    assert "Nightjar — Bird Call Classifier (Solo)" in by_label
    assert "Tidepool — Group Project" in by_label


def test_a_year_range_spans_january_to_december(by_label):
    entry = by_label["Nightjar — Bird Call Classifier (Solo)"]
    assert entry["start"] == "2024-01"
    assert entry["end"] == "2025-12"


def test_a_lone_year_is_that_whole_year(by_label):
    entry = by_label["Tidepool — Group Project"]
    assert entry["start"] == "2023-01"
    assert entry["end"] == "2023-12"


def test_a_year_in_the_summary_paragraph_is_not_an_engagement():
    """The reason the year anchor is bounded to a PROJECTS section. A
    document-wide year scan reads a date in the PROFILE prose as an engagement
    and invents one out of the summary."""
    text = (
        "Ada Okonkwo Engineer  |  ada@example.com "
        "PROFILE Graduated in 2020 and has shipped since. "
        "EXPERIENCE Analyst — Somewhere Jan 2021 – Feb 2022 Remote "
        "• Did the work and wrote it down. "
    )
    entries = ri.parse_engagements(text, [])
    assert len(entries) == 1
    assert entries[0]["label"] == "Analyst — Somewhere"


def test_a_project_sections_kind_is_taken_from_the_section(by_label):
    """Forced, not guessed: the keyword vote on the label reads "Nightjar — Bird
    Call Classifier (Solo)" as work."""
    assert by_label["Nightjar — Bird Call Classifier (Solo)"]["kind"] == "project"
    assert by_label["Tidepool — Group Project"]["kind"] == "project"


def test_kind_is_decided_by_the_label_not_the_prose_under_it(by_label):
    """Reading both returned education for a developer role whose bullets
    happened to mention a university."""
    assert by_label["Senior Engineer — Northwind"]["kind"] == "work"
    assert by_label["University of Somewhere — B.S. Computer Science"]["kind"] == "education"


def test_an_ongoing_role_ends_at_a_real_month_never_the_word_present(by_label):
    """prompt_month refuses anything that is not YYYY-MM and loops until it gets
    one, so an entry defaulting to "present" traps anyone pressing Enter."""
    end = by_label["Senior Engineer — Northwind"]["end"]
    assert sp.month_index(end)  # parses at all
    assert end == ri._this_month()


def test_every_proposed_date_is_one_prompt_month_would_accept(career):
    for entry in career:
        for key in ("start", "end"):
            assert sp.month_index(entry[key])
        assert sp.month_index(entry["end"]) >= sp.month_index(entry["start"])


def test_every_proposed_kind_is_one_the_arc_can_colour(career):
    for entry in career:
        assert entry["kind"] in sp.KINDS


# --------------------------------------------------------------------------
# render_resume_md
# --------------------------------------------------------------------------

def test_the_corpus_file_carries_the_bullets(career):
    seed = [dict(entry) for entry in career]
    bullets = {entry["label"]: entry.pop("bullets") for entry in seed}
    md = sp.render_resume_md({"name": "Ada P. Okonkwo", "career": seed}, bullets)
    assert "- Cut p99 latency from 900ms to 120ms by batching at the edge." in md
    assert "## Projects" in md


def test_a_renamed_engagement_loses_its_bullets_rather_than_taking_anothers(career):
    """Bullets are joined by label, so a miss has to be quiet and empty."""
    seed = [dict(entry) for entry in career]
    bullets = {entry["label"]: entry.pop("bullets") for entry in seed}
    seed[0]["label"] = "Renamed By Hand"
    md = sp.render_resume_md({"name": "Ada", "career": seed}, bullets)
    assert "### Renamed By Hand" in md
    assert "Coursework: Algorithms" not in md


def test_the_corpus_file_is_unchanged_without_bullets(career):
    """The no-import path still works and still emits no invented prose."""
    seed = [{k: v for k, v in entry.items() if k != "bullets"} for entry in career]
    md = sp.render_resume_md({"name": "Ada", "career": seed})
    assert "### Senior Engineer — Northwind" in md
    assert "Cut p99 latency" not in md


# --------------------------------------------------------------------------
# Producer shapes: Google Docs and Chrome
# --------------------------------------------------------------------------
# Measured against ten generated résumés with answer keys, exported through both
# producers (tools/score_resume_import.py). Each fixture below is the shape that
# producer's PDF actually extracts to, cut down.

# Google Docs: two spaces between *every* word, headings in the case typed.
GDOCS = (
    "David  Okafor  II  \nMarketing  Manager  \n"
    "Experience  \n \n"
    "Marketing  Manager  —  Harbor  &  Vine  Brands  Mar  2018  –  Present  \n"
    "●  Own  a  multi-channel  budget  across  Email  and  Paid  Search.  \n"
)


def test_google_docs_double_spacing_is_collapsed():
    text = ri.normalize_extraction(GDOCS)
    assert "Marketing Manager — Harbor & Vine Brands Mar 2018 – Present" in text


def test_a_title_case_heading_line_is_upper_cased():
    assert "\nEXPERIENCE\n" in ri.normalize_extraction(GDOCS)


def test_occasional_double_spaces_are_still_layout_gaps():
    """The owner's own PDF uses a double space as its only boundary between one
    entry's last bullet and the next label. Collapsing it glued a fraternity to
    a school, so doubles survive when they are not the normal separator."""
    assert "Chess Club  Riverside" in ri.normalize_extraction(RESUME)


def test_a_sentence_mentioning_a_section_is_not_a_heading():
    text = ri.normalize_extraction("I gained experience in projects.\n")
    assert "EXPERIENCE" not in text


# Chrome with `float: right` dates: every date is written before the text.
HOISTED = "Mar 2018 – Present\nMay 2015 – Feb 2018\n2021\n2011 – 2015\nDavid Okafor II\n"


def test_a_run_of_date_only_lines_is_read_as_hoisted_floats():
    assert ri._dates_hoisted(HOISTED)
    assert ri._dates_hoisted("Jane Doe\nSKILLS\nPython\n" + HOISTED)


def test_ordinary_text_is_not_read_as_hoisted():
    assert not ri._dates_hoisted(RESUME)
    assert not ri._dates_hoisted("Label\nMar 2018 – Present\nA bullet.\nLabel two\n2021\n")


# Layout-mode text, one visual row per line, as pypdf prints a Chrome page.
TWO_COLUMN = "\n".join([
    "Jane Doe",
    "Augusta, GA | jane@example.com | LinkedIn: https://www.linkedin.com/in/janedoe-example",
    "SKILLS                                        SUMMARY",
    "Operations:                                   Manager with ten years in hospitality.",
    "P&L Management, Inventory                     EXPERIENCE",
    "Control, Food Safety                          General Manager — Magnolia Grill        03/2015 - present",
    "Leadership:                                      Own the P&L for a 35-person team and its budget.",
    "Team Leadership, Training                     Assistant Manager — Peach State Kitchen",
    "Scheduling                                       Trained 20 hires and owned Scheduling    03/2010 - 03/2015",
    "                                                 for the whole of the front of house.",
    "                                              EDUCATION",
    "                                              B.S. Hospitality — Peachtree University",
])


def test_a_two_column_page_is_read_one_column_at_a_time():
    lines = ri.split_columns(TWO_COLUMN).splitlines()
    assert lines.index("Scheduling") < lines.index("SUMMARY")
    assert lines.index("Control, Food Safety") == lines.index("P&L Management, Inventory") + 1


def test_a_right_aligned_date_joins_its_label_with_one_space():
    lines = ri.split_columns(TWO_COLUMN).splitlines()
    assert "General Manager — Magnolia Grill 03/2015 - present" in lines


def test_a_date_that_wrapped_onto_a_bullet_goes_back_to_its_label():
    lines = ri.split_columns(TWO_COLUMN).splitlines()
    assert "Assistant Manager — Peach State Kitchen 03/2010 - 03/2015" in lines
    assert "Trained 20 hires and owned Scheduling" in lines


def test_a_single_column_page_finds_no_gutter():
    """A two-character run between mid-sentence words once passed for a gutter,
    because most lines ended before reaching it."""
    page = "\n".join([
        "Marketing Manager — Harbor & Vine Brands" + " " * 50 + "Mar 2018 – Present",
        "   Own a multi-channel budget across Email Marketing, Paid Search, and Social Media.",
        "   Attributed 2.1 million dollars in revenue using Google Analytics dashboards here.",
        "Marketing Specialist — Beacon Media Group" + " " * 49 + "May 2015 – Feb 2018",
        "   Managed an email program with 250,000 subscribers in Mailchimp for the brand.",
        "   Improved organic sessions 65 percent with an SEO Content Strategy that stuck.",
        "Charity 5K Campaign" + " " * 75 + "2021",
        "   Led pro-bono Brand Strategy for a local nonprofit race; 1,200 runners registered.",
    ])
    assert ri._gutter(page.splitlines()) is None


def test_the_first_bullet_survives_when_the_date_ends_its_line():
    span = "\nLed migration of the dashboard to React.\nDesigned Python microservices on AWS.\n"
    assert ri.extract_bullets(span) == [
        "Led migration of the dashboard to React.",
        "Designed Python microservices on AWS.",
    ]


def test_a_header_on_the_dates_own_line_is_still_dropped():
    span = " Athens, GA  •  Built a detector that ran at 30 frames per second.  •  Wrote the paper."
    bullets = ri.extract_bullets(span)
    assert not any("Athens" in b for b in bullets)


# The shape Chrome extracts a résumé dated by numeric month in: one field per
# line, the date at the end of the label's line, bullets with no glyph.
NUMERIC = (
    "Marcus Webb\nData Scientist\nChicago, IL | marcus.webb@example.com\n"
    "SUMMARY\nData scientist with four years of experience turning messy data "
    "into models.\nEXPERIENCE\n"
    "Data Scientist — Meridian Analytics 03/2021 - present\n"
    "Built a churn prediction model in Python with scikit-learn.\n"
    "Data Analyst — Lakeshore Insights 06/2019 - 02/2021\n"
    "Built executive dashboards in Tableau for weekly leadership reviews.\n"
    "EDUCATION\nM.S. Data Science — Northwood University 2017 - 2019\n"
    "Thesis on gradient-boosted churn models.\n"
)


def test_a_numeric_month_range_anchors_an_engagement():
    found = {e["label"]: e for e in ri.parse_engagements(NUMERIC, [])}
    analyst = found["Data Analyst — Lakeshore Insights"]
    assert (analyst["start"], analyst["end"]) == ("2019-06", "2021-02")
    assert found["Data Scientist — Meridian Analytics"]["end"] == ri._this_month()


def test_a_numeric_month_is_not_read_out_of_a_ratio_or_a_longer_date():
    # "24/7" is not a month, and "12/03/2021" is a day, not "03/2021".
    text = "EXPERIENCE\nSOC Analyst 24/7 coverage 12/03/2021 - present\n"
    assert not ri._numeric_anchors(text, [])


def test_a_year_range_outside_projects_anchors_an_engagement():
    found = {e["label"]: e for e in ri.parse_engagements(NUMERIC, [])}
    degree = found["M.S. Data Science — Northwood University"]
    assert (degree["start"], degree["end"]) == ("2017-01", "2019-12")


@pytest.mark.parametrize("dates", ["2018-2021", "2015 – 2019", "2021 - present"])
def test_every_year_range_spelling_anchors_anywhere(dates):
    text = f"EDUCATION\nB.S. Statistics — Northwood University {dates}\nMinor in economics.\n"
    assert len(ri.parse_engagements(text, [])) == 1


def test_a_bare_year_outside_projects_still_anchors_nothing():
    text = "EXPERIENCE\nAnalyst — Helix Networks, promoted in 2019 after a year.\n"
    assert ri.parse_engagements(text, []) == []


def test_kind_is_taken_from_the_section_the_entry_sits_under():
    # "Research Assistant — Northwood University" loses the keyword vote to
    # "university" and reads as education. It sits under EXPERIENCE.
    text = (
        "EXPERIENCE\nResearch Assistant — Northwood University 09/2017 - 05/2019\n"
        "Cleaned survey data in Python.\nEDUCATION\n"
        "Data Science Bootcamp — Halcyon Labs 2016 - 2017\nCapstone on churn.\n"
    )
    kinds = [e["kind"] for e in ri.parse_engagements(text, [])]
    assert kinds == ["work", "education"]


def test_kind_falls_back_to_the_label_when_no_heading_precedes_it():
    text = "B.S. Statistics — Northwood University 2013 - 2017\nMinor in economics.\n"
    assert ri.parse_engagements(text, [])[0]["kind"] == "education"


def test_a_heading_that_says_nothing_about_kind_leaves_the_guess_alone():
    text = "AWARDS\nB.S. Statistics — Northwood University 2013 - 2017\nDean's list.\n"
    assert ri.parse_engagements(text, [])[0]["kind"] == "education"


# Google Docs' shape: the role and the contact line share a line, separated by
# one space, and the contact line opens with a street address.
DOCS_HEADER = (
    "Marcus Webb\nData Scientist Chicago, IL, 4821 Lakewood Avenue, Apt 6B | "
    "(312) 555-0147 | marcus.webb@example.com\nSKILLS\nLanguages:\nPython, R\n"
)


def test_the_role_stops_where_the_location_begins():
    found = ri.parse_contact(DOCS_HEADER)
    assert found["role"] == "Data Scientist"
    assert found["location"] == "Chicago, IL"


@pytest.mark.parametrize("address", [
    "4821 Lakewood Avenue, Apt 6B",
    "8820 Wilson Boulevard",
    "12 Old Mill Rd. #4",
    "301 Market St Suite 200",
])
def test_street_addresses_are_stripped_from_corpus_bound_text(address):
    assert ri.scrub_private(f"Chicago, IL, {address} | Data") == "Chicago, IL, | Data"


def test_a_number_before_a_capitalised_phrase_is_not_an_address():
    claim = "Cut 40 Splunk alerts per day to 12 with tuned rules."
    assert ri.scrub_private(claim) == claim.strip(" |·•,;-")


def test_a_role_that_ran_into_an_address_is_scrubbed_on_the_way_to_the_corpus():
    md = sp.render_resume_md({"name": "Marcus Webb",
                              "role": "Data Scientist 4821 Lakewood Avenue, Apt 6B"})
    assert "Lakewood" not in md
    assert "- Role: Data Scientist" in md


def test_a_heading_glued_onto_the_last_bullet_is_split_off_and_upper_cased():
    # Google Docs' shape for a heading that follows a list.
    text = ri.normalize_extraction(
        "● Cleaned survey data in Python and co-authored two conference posters. Education\n"
        "\nM.S. Data Science — Northwood University 2017 - 2019\n"
    )
    assert "posters.\nEDUCATION\n" in text


def test_a_sentence_ending_on_a_section_word_mid_line_is_not_a_heading():
    line = "● Led the team. Experience with Splunk helped.\n"
    assert ri.normalize_extraction(line) == line.rstrip("\n")


# A skills section whose group names carry no colon, in Chrome's shape: the
# name on its own line, the members on the next, a summary straight after.
NO_COLON_SKILLS = (
    "SKILLS\nPlatforms\nLinux, AWS, Azure\nInfrastructure as Code\nTerraform, Ansible\n"
    "SUMMARY\nDevOps engineer who sweats the\ndetails: monitoring, alerts, and cost.\n"
)


def test_a_group_name_without_a_colon_is_read_off_its_own_line():
    groups = ri.parse_skill_section(NO_COLON_SKILLS)
    assert list(groups) == ["Platforms", "Infrastructure as Code"]
    assert groups["Infrastructure as Code"] == ["Terraform", "Ansible"]


def test_the_skills_section_stops_at_the_summary():
    # "details: monitoring, alerts" read as a group called "details".
    text = "SKILLS\nCore:\nHTML5, CSS3\nSUMMARY\nI sweat the\ndetails: semantics, keyboard flow.\n"
    assert list(ri.parse_skill_section(text)) == ["Core"]


def test_a_one_letter_language_survives_as_a_skill():
    assert ri.parse_skill_section("SKILLS\nLanguages\nPython, R, SQL\n")["Languages"] == ["Python", "R", "SQL"]
