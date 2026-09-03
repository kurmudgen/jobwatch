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


def test_onsite_with_remote_is_kept():
    job = posting(location="Hybrid - Austin, TX",
                  description_text="Hybrid role, remote two days a week.")
    assert filters.match_onsite(job["location"], job["description_text"]) is None
    assert filters.evaluate(job) is not None


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
