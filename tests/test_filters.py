"""Filter rules: what gets included, what gets dropped, what gets flagged."""
import pytest

import filters


def posting(**kwargs):
    base = {
        "source": "test",
        "company": "Acme",
        "title": "Support Engineer",
        "location": "Remote - US",
        "remote": True,
        "employment_type": "Full-time",
        "url": "https://example.com/jobs/1",
        "posted_at": "2026-09-01T00:00:00+00:00",
        "description_text": "Help customers debug integrations.",
    }
    base.update(kwargs)
    return base


# --- include ---------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Support Engineer", "support engineer"),
    ("Customer Support Engineer", "customer support engineer"),
    ("Technical Support Engineer II", "technical support engineer"),
    ("Solutions Engineer, EMEA", "solutions engineer"),
    ("Sales Engineer", "sales engineer"),
    ("Implementation Engineer", "implementation engineer"),
    ("Forward Deployed Software Engineer", "forward deployed"),
    ("Deployed Engineer", "deployed engineer"),
    ("Customer Engineer", "customer engineer"),
    ("Technical Account Manager", "technical account manager"),
    ("Developer Support Specialist", "developer support"),
    ("Developer Advocate", "developer advocate"),
    ("Integration Engineer", "integration engineer"),
    ("Onboarding Engineer", "onboarding engineer"),
])
def test_every_include_keyword_matches(title, expected):
    assert filters.match_include(title) == expected


def test_include_is_case_insensitive_and_whitespace_tolerant():
    assert filters.match_include("SENIOR   SUPPORT-ENGINEER") == "support engineer"


def test_most_specific_keyword_wins():
    assert filters.match_include("Technical Support Engineer") == "technical support engineer"
    assert filters.match_include("Customer Support Engineer") == "customer support engineer"


@pytest.mark.parametrize("title", [
    "AI Engineer",
    "Applied AI Engineer",
    "Senior Applied AI Engineer, Inference",
])
def test_ai_engineer_titles_are_not_included(title):
    """At these companies these are senior ML roles, not customer-facing ones."""
    assert filters.match_include(title) is None
    assert filters.evaluate(posting(title=title)) is None


def test_unrelated_title_does_not_match():
    assert filters.match_include("Senior Backend Engineer") is None
    assert filters.match_include("Product Designer") is None


def test_evaluate_keeps_a_clean_match_and_records_the_keyword():
    result = filters.evaluate(posting(title="Solutions Engineer"))
    assert result is not None
    assert result["matched_keyword"] == "solutions engineer"
    assert result["matched_in"] == "title"


def test_description_matching_is_opt_in():
    job = posting(title="Backend Engineer",
                  description_text="You will work alongside our solutions engineer team.")
    assert filters.evaluate(job) is None
    opted_in = filters.evaluate(job, match_description=True)
    assert opted_in["matched_keyword"] == "solutions engineer"
    assert opted_in["matched_in"] == "description"


# --- exclude ---------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Contract Support Engineer", "contract"),
    ("Support Engineer (Contractor)", "contractor"),
    ("Support Engineer Intern", "intern"),
    ("Solutions Engineer Internship", "internship"),
    ("Staff Support Engineer", "staff"),
    ("Principal Solutions Engineer", "principal"),
    ("Director of Solutions Engineering", "director"),
    ("Support Engineering Manager", "manager"),
    ("VP, Solutions Engineering", "vp"),
    ("Head of Developer Support", "head of"),
])
def test_excluded_titles(title, expected):
    assert filters.match_exclude(title) == expected
    assert filters.evaluate(posting(title=title)) is None


@pytest.mark.parametrize("employment_type", ["Contract", "Part-time", "Part time", "Internship"])
def test_employment_type_excludes(employment_type):
    assert filters.match_exclude("Support Engineer", employment_type) is not None
    assert filters.evaluate(posting(employment_type=employment_type)) is None


def test_technical_account_manager_survives_the_manager_exclusion():
    assert filters.match_exclude("Technical Account Manager") is None
    result = filters.evaluate(posting(title="Technical Account Manager"))
    assert result is not None
    assert result["matched_keyword"] == "technical account manager"


@pytest.mark.parametrize("title", [
    "Support Engineer, Internal Tools",   # "internal", not "intern"
    "Solutions Engineer, VPC Networking",  # "vpc", not "vp"
])
def test_exclusions_respect_word_boundaries(title):
    assert filters.match_exclude(title) is None


# --- on-site exclusion -----------------------------------------------------

@pytest.mark.parametrize("location,description", [
    ("Austin, TX (Hybrid)", "Join our team."),
    ("New York", "This is an on-site role."),
    ("Seattle", "Onsite four days a week."),
    ("Boston", "You will be in office daily."),
    ("Denver", "In-office collaboration is expected."),
])
def test_onsite_without_remote_is_dropped(location, description):
    assert filters.match_onsite(location, description) is not None
    assert filters.evaluate(posting(location=location, description_text=description)) is None


def test_remote_in_the_location_itself_keeps_the_posting():
    job = posting(location="Hybrid - Remote, US",
                  description_text="Join the team.")
    assert filters.match_onsite(job["location"], job["description_text"]) is None
    assert filters.evaluate(job) is not None


def test_a_role_level_remote_claim_overrides_an_onsite_location():
    """Vercel tags a location "Hybrid - London, Berlin" on a role whose
    description says "this role is remote-first". That one stays."""
    job = posting(location="Hybrid - London, Berlin",
                  description_text="This role is remote-first with occasional travel.")
    assert filters.match_onsite(job["location"], job["description_text"]) is None
    assert filters.evaluate(job) is not None


def test_boilerplate_remote_does_not_rescue_an_onsite_location():
    """Palantir ships "there are a few roles that allow for Remote work on an
    exceptional basis" in every description. It must not rescue "(onsite)"."""
    job = posting(
        location="Washington, D.C. (onsite)",
        description_text=(
            "Based on business need, there are a few roles that allow for "
            "remote work on an exceptional basis."
        ),
    )
    assert filters.match_onsite(job["location"], job["description_text"]) == "onsite"
    assert filters.evaluate(job) is None


def test_an_onsite_term_only_in_the_description_is_still_rescued_by_remote():
    job = posting(location="United States",
                  description_text="Hybrid optional. This is a remote team.")
    assert filters.match_onsite(job["location"], job["description_text"]) is None


def test_every_strong_remote_phrase_is_multi_word():
    """A one-word entry collapses to a bare remote and rescues everything."""
    for phrase in filters.STRONG_REMOTE_PHRASES:
        assert len(phrase.split()) >= 2, phrase


# --- flags -----------------------------------------------------------------

@pytest.mark.parametrize("description,fragment", [
    ("Applicants in Alaska are not eligible.", "eligibility"),
    ("Open to all US states, excluding New York.", "eligibility"),
    ("Anywhere in the US except where noted.", "eligibility"),
])
def test_eligibility_phrases_flag(description, fragment):
    flags = filters.compute_flags(description)
    assert any(f.startswith(fragment) for f in flags)


def test_a_list_of_states_flags_for_manual_montana_check():
    description = "We hire in California, Texas, New York and Washington."
    flags = filters.compute_flags(description)
    states_flag = next(f for f in flags if f.startswith("states:"))
    assert "california" in states_flag and "texas" in states_flag
    assert "montana" not in states_flag


def test_a_single_state_mention_does_not_flag():
    assert filters.compute_flags("Our office is in Montana.") == []


@pytest.mark.parametrize("description,expected", [
    ("Must hold an active security clearance.", "clearance"),
    ("TS/SCI required.", "ts/sci"),
    ("Applicants need a Secret clearance.", "secret"),
    ("Supporting DoD customers.", "dod"),
])
def test_clearance_flags(description, expected):
    flags = filters.compute_flags(description)
    clearance = next(f for f in flags if f.startswith("clearance:"))
    assert expected in clearance


def test_secretary_does_not_trip_the_secret_flag():
    assert filters.compute_flags("Report to the Secretary of the department.") == []


@pytest.mark.parametrize("description", [
    "Our globally distributed team is our secret weapon.",
    "We keep secrets safe with our secret management tooling.",
])
def test_secret_outside_a_clearance_context_does_not_flag(description):
    """This flagged every Supabase support role as needing a clearance."""
    assert filters.compute_flags(description) == []


@pytest.mark.parametrize("description", [
    "Must hold an active Secret clearance.",
    "Requires clearance at the Secret level.",
    "Top Secret required.",
])
def test_secret_in_a_clearance_context_does_flag(description):
    flags = filters.compute_flags(description)
    assert any(f.startswith("clearance:") for f in flags)


def test_flagged_postings_are_kept_not_dropped():
    job = posting(description_text="Requires TS/SCI. Remote from CONUS.")
    result = filters.evaluate(job)
    assert result is not None
    assert any(f.startswith("clearance:") for f in result["flags"])


def test_evaluate_all_returns_only_survivors():
    batch = [
        posting(title="Support Engineer", url="https://example.com/1"),
        posting(title="Staff Support Engineer", url="https://example.com/2"),
        posting(title="Backend Engineer", url="https://example.com/3"),
    ]
    kept = filters.evaluate_all(batch)
    assert [p["url"] for p in kept] == ["https://example.com/1"]


# --- tiers -----------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Support Engineer",
    "Customer Support Engineer",
    "Technical Support Engineer",
    "Solutions Engineer, EMEA",
    "Implementation Engineer",
    "Technical Account Manager",
    "Developer Support Specialist",
    "Support Engineer II",   # II is mid-level, not seniority
])
def test_tier_one_is_core_targets_without_seniority(title):
    assert filters.compute_tier(title) == 1


@pytest.mark.parametrize("title", [
    "Forward Deployed Engineer",
    "Forward Deployed Software Engineer",
    "Deployed Engineer",
    "Customer Engineer",
])
def test_tier_two_is_fde_and_customer_engineer(title):
    assert filters.compute_tier(title) == 2


@pytest.mark.parametrize("title", [
    "Sales Engineer",
    "Integration Engineer",
    "Developer Advocate",
    "Onboarding Engineer",
])
def test_tier_three_is_everything_else(title):
    assert filters.compute_tier(title) == 3


@pytest.mark.parametrize("title", [
    "Senior Solutions Engineer",
    "Sr. Support Engineer",
    "Lead Implementation Engineer",
    "Chief Solutions Engineer",
    "Distinguished Solutions Engineer",
    "Support Engineer III",
])
def test_seniority_demotes_a_tier_one_title_to_tier_three(title):
    assert filters.match_seniority(title) is not None
    assert filters.compute_tier(title) == 3


def test_seniority_respects_word_boundaries():
    # "Sri Lanka" must not read as "Sr", "Leading" must not read as "Lead".
    assert filters.match_seniority("Support Engineer, Sri Lanka") is None
    assert filters.match_seniority("Support Engineer, Leading Platform") is None


def test_evaluate_attaches_the_tier():
    assert filters.evaluate(posting(title="Support Engineer"))["tier"] == 1
    assert filters.evaluate(posting(title="Customer Engineer"))["tier"] == 2
    assert filters.evaluate(posting(title="Sales Engineer"))["tier"] == 3


# --- US-remote filter ------------------------------------------------------

@pytest.mark.parametrize("location", [
    "Remote",
    "Remote, Global",
    "Remote - US",
    "Remote, AMER",
    "United States (Remote)",
    "United States - Remote",
    "Seattle, WA, Remote-US",
    "Remote, New York, San Francisco",
    "Colorado, USA, Remote",
    "US-NY-New York, US-NJ Metro-Remote, US-NY-Remote",
    "US-IL-Remote, Dallas, TX, US-CO-Denver",
    "Remote in the US, Remote in Canada",
])
def test_us_remote_keeps(location):
    assert filters.is_us_remote(location) is True


@pytest.mark.parametrize("location", [
    "Remote - Colombia",
    "Remote, EMEA",
    "Remote (EMEA)",
    "Remote, APAC",
    "Remote - India",
    "Remote - Japan",
    "United Kingdom, Remote",
    "France (Remote)",
    "MX-Mexico-Remote",
    "Ontario - Remote",
    "Singapore - Remote",
    "Remote - United Kingdom, Germany",
    "EU | Remote",
])
def test_us_remote_drops_foreign(location):
    assert filters.is_us_remote(location) is False


@pytest.mark.parametrize("location", [
    "San Francisco, CA",                       # US but not remote
    "Costa Mesa, California, United States",   # US but not remote
    "London",
    "Distributed",
    "Hybrid - London, Berlin",
])
def test_us_remote_requires_the_word_remote(location):
    assert filters.is_us_remote(location) is False


def test_a_multi_region_posting_that_includes_north_america_is_kept():
    """Strong US tokens win over a co-listed EMEA/APAC."""
    assert filters.is_us_remote("Remote - NA, APAC, EMEA") is True
    assert filters.is_us_remote("Remote - EMEA, Remote - NA") is True


def test_canadian_ontario_is_not_read_as_california():
    """"CA-Ontario-Toronto" must read as Canada, not the CA state code."""
    assert filters.is_us_remote("CA-Ontario-Toronto Remote") is False


@pytest.mark.parametrize("location", [
    "Remote in Europe",     # "in" must not read as Indiana
    "Remote or nothing",    # "or" must not read as Oregon
    "Remote, ok then",      # "ok" must not read as Oklahoma
    "Remote, hi there",     # "hi" must not read as Hawaii
    "Remote, call me",      # "me" must not read as Maine
])
def test_lowercase_english_words_are_not_read_as_state_codes(location):
    """These must not register as a US signal. Whether the posting is then kept
    is a separate question - "Remote in Europe" is dropped for naming Europe,
    while "Remote or nothing" is kept for naming no country at all."""
    assert filters.has_us_marker(location) is False


def test_remote_naming_no_country_is_kept():
    assert filters.is_us_remote("Remote or nothing") is True
    assert filters.is_us_remote("Remote in Europe") is False


def test_filter_us_remote_over_a_batch():
    batch = [
        posting(url="https://example.com/1", location="Remote, AMER"),
        posting(url="https://example.com/2", location="Remote - India"),
        posting(url="https://example.com/3", location="London"),
    ]
    assert [p["url"] for p in filters.filter_us_remote(batch)] == ["https://example.com/1"]


# --- dedupe ----------------------------------------------------------------

def test_dedupe_keeps_the_us_copy_of_a_cloned_req():
    batch = [
        posting(url="https://example.com/de", company="ElevenLabs",
                title="Enterprise Solutions Engineer", location="Germany"),
        posting(url="https://example.com/na", company="ElevenLabs",
                title="Enterprise Solutions Engineer", location="Remote, United States"),
        posting(url="https://example.com/jp", company="ElevenLabs",
                title="Enterprise Solutions Engineer", location="Japan"),
    ]
    kept = filters.dedupe(batch)
    assert len(kept) == 1
    assert kept[0]["url"] == "https://example.com/na"


def test_dedupe_leaves_distinct_titles_alone():
    batch = [
        posting(url="https://example.com/1", company="Acme", title="Support Engineer"),
        posting(url="https://example.com/2", company="Acme", title="Solutions Engineer"),
    ]
    assert len(filters.dedupe(batch)) == 2


def test_dedupe_does_not_merge_across_companies():
    batch = [
        posting(url="https://example.com/1", company="Acme", title="Support Engineer"),
        posting(url="https://example.com/2", company="Globex", title="Support Engineer"),
    ]
    assert len(filters.dedupe(batch)) == 2


def test_dedupe_is_case_and_whitespace_insensitive_on_the_title():
    batch = [
        posting(url="https://example.com/1", company="Acme",
                title="Support  Engineer", location="Berlin"),
        posting(url="https://example.com/2", company="Acme",
                title="SUPPORT ENGINEER", location="Remote, US"),
    ]
    kept = filters.dedupe(batch)
    assert len(kept) == 1
    assert kept[0]["url"] == "https://example.com/2"


# --- html digest -----------------------------------------------------------

def test_html_digest_escapes_and_links():
    import digest
    html = digest.render_html([
        posting(company="Acme & Co", title="Support <Engineer>",
                url="https://example.com/jobs?gh_jid=1", location="Remote, AMER"),
    ], title="jobwatch")
    # Company and title are escaped, the URL is a real anchor.
    assert "Acme &amp; Co" in html
    assert "&lt;Engineer&gt;" in html
    assert "<a href='https://example.com/jobs?gh_jid=1'" in html
    # The "=" in the query string must survive verbatim; the plain-text path
    # through the Gmail connector eats it.
    assert "gh_jid=1" in html
    assert "<script" not in html


def test_html_digest_handles_no_matches():
    import digest
    html = digest.render_html([], empty_note="Nothing new.")
    assert "Nothing new." in html
    assert "<ul" not in html


# --- federal digest section ------------------------------------------------

def federal(**kwargs):
    base = {
        "source": "usajobs", "company": "CISA", "title": "IT Specialist (Customer Support)",
        "location": "Anywhere in the U.S. (remote job)", "remote": True,
        "employment_type": "Full-time", "url": "https://www.usajobs.gov/job/1",
        "posted_at": "2026-09-01T00:00:00+00:00", "description_text": "",
        "flags": [], "salary_min": 100000.0, "salary_max": 130000.0,
    }
    base.update(kwargs)
    return base


def test_federal_postings_get_their_own_section_sorted_by_salary_max():
    import digest
    rows = [
        federal(url="https://u/1", title="Low", salary_max=110000.0),
        federal(url="https://u/2", title="High", salary_max=180000.0),
        federal(url="https://u/3", title="Mid", salary_max=140000.0),
    ]
    md = digest.render(rows, by_tier=True)
    assert "## Federal (USAJOBS) (3)" in md
    assert md.index("High") < md.index("Mid") < md.index("Low")


def test_federal_postings_without_a_salary_sort_last():
    import digest
    rows = [
        federal(url="https://u/1", title="NoSalary", salary_min=None, salary_max=None),
        federal(url="https://u/2", title="HasSalary", salary_max=120000.0),
    ]
    md = digest.render(rows, by_tier=True)
    assert md.index("HasSalary") < md.index("NoSalary")
    assert "salary not published" in md


def test_federal_postings_are_excluded_from_the_tier_sections():
    import digest
    rows = [
        posting(title="Support Engineer", url="https://x/1"),
        federal(url="https://u/1"),
    ]
    md = digest.render(rows, by_tier=True)
    # The federal role is a support-engineer title but must not appear in Tier 1.
    tier_block = md[md.index("## Tier 1"):md.index("## Federal")]
    assert "CISA" not in tier_block
    assert "2 matches" in md
    assert "Federal: 1" in md


def test_federal_section_shows_the_not_open_flag():
    import digest
    rows = [federal(flags=["not-open:current federal employees only"])]
    md = digest.render(rows)
    assert "not-open:current federal employees only" in md
    html = digest.render_html(rows)
    assert "not-open:current federal employees only" in html


def test_federal_only_digest_still_gets_a_summary():
    import digest
    md = digest.render([federal()], by_tier=True)
    assert "1 match across 1 companies" in md
    assert "Federal: 1" in md
    html = digest.render_html([federal()])
    assert "Federal: 1" in html


def test_salary_text_formats_ranges():
    import digest
    assert digest._salary_text(federal(salary_min=100000.0, salary_max=130000.0)) \
        == "$100,000 - $130,000"
    assert digest._salary_text(federal(salary_min=None, salary_max=130000.0)) \
        == "up to $130,000"
    assert digest._salary_text(federal(salary_min=100000.0, salary_max=None)) \
        == "from $100,000"
    assert digest._salary_text(federal(salary_min=None, salary_max=None)) \
        == "salary not published"


# --- federal include gate --------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("IT Specialist (Customer Support)", "customer support"),
    ("IT Specialist (Applications Software)", "applications"),
    ("IT Cybersecurity Specialist (INFOSEC)", "cybersecurity"),
    ("IT Specialist (Solutions Analysis)", "solutions"),
    ("IT Specialist (CUSTSPT)", "it specialist (2210)"),
    ("IT Specialist (SYSANALYSIS)", "it specialist (2210)"),
])
def test_usajobs_keyword_labels(title, expected):
    assert filters.usajobs_keyword(title) == expected


def test_federal_titles_pass_on_the_api_query_not_the_engineer_list():
    """Federal 2210 titles contain no "engineer", so the private-sector include
    list matches almost none of them. Without this gate the whole source would
    be silently discarded."""
    job = federal(title="IT Specialist (CUSTSPT)", description_text="Help desk work.")
    assert filters.match_include(job["title"]) is None
    result = filters.evaluate(job)
    assert result is not None
    assert result["matched_in"] == "usajobs query"
    assert result["matched_keyword"] == "it specialist (2210)"


def test_the_federal_gate_does_not_leak_to_other_sources():
    job = posting(source="greenhouse", title="IT Specialist (CUSTSPT)")
    assert filters.evaluate(job) is None


def test_a_title_match_still_wins_over_the_federal_gate():
    job = federal(title="IT Specialist (Solutions Engineer)")
    result = filters.evaluate(job)
    assert result["matched_keyword"] == "solutions engineer"
    assert result["matched_in"] == "title"


@pytest.mark.parametrize("title", [
    "Supervisory IT Specialist (Customer Support)",
    "Supervisory Solutions Engineer",
])
def test_supervisory_is_excluded_as_the_federal_spelling_of_manager(title):
    assert filters.match_exclude(title) == "supervisory"
    assert filters.evaluate(federal(title=title)) is None


def test_federal_exclusions_and_flags_still_apply():
    """The gate replaces the include check only."""
    assert filters.evaluate(federal(title="IT Specialist (Customer Support) Intern")) is None
    kept = filters.evaluate(federal(
        title="IT Specialist (CUSTSPT)",
        description_text="Open to current federal employees only. Requires a Secret clearance.",
    ))
    assert kept is not None
    assert "not-open:current federal employees only" in kept["flags"]
    assert any(f.startswith("clearance:") for f in kept["flags"])


def test_salary_survives_the_database_round_trip(tmp_path):
    """The Federal section sorts on salary_max, and `list` reads from SQLite -
    so the column has to exist or the sort silently degrades to alphabetical."""
    import store
    conn = store.connect(tmp_path / "t.db")
    try:
        store.upsert_many(conn, [federal(url="https://u/1", salary_min=1.0, salary_max=2.0)])
        rows = store.recent(conn, days=1)
        assert rows[0]["salary_min"] == 1.0
        assert rows[0]["salary_max"] == 2.0
    finally:
        conn.close()


def test_migration_is_additive_and_idempotent(tmp_path):
    """An existing database must keep every row when the columns are added."""
    import sqlite3, store
    path = tmp_path / "old.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(
        store.SCHEMA.replace("    salary_min       REAL,\n", "")
                    .replace("    salary_max       REAL,\n", "")
    )
    raw.execute(
        "INSERT INTO postings (url, source, company, title, first_seen, last_seen) "
        "VALUES ('https://u/1','ashby','Acme','Support Engineer','x','x')"
    )
    raw.commit()
    raw.close()

    conn = store.connect(path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(postings)")}
        assert {"salary_min", "salary_max"} <= cols
        assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 1
        assert store._migrate(conn) == []
    finally:
        conn.close()
