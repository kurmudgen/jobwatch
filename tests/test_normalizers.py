"""One normalizer per source, driven by small payloads saved from the live APIs."""
import json
from pathlib import Path

import pytest

import sources

FIXTURES = Path(__file__).parent / "fixtures"

REQUIRED_FIELDS = set(sources.FIELDS)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def assert_shape(postings):
    """Every normalizer must emit exactly the common posting shape."""
    assert postings, "normalizer produced no postings"
    for posting in postings:
        assert set(posting) == REQUIRED_FIELDS
        assert posting["url"].startswith("http")
        assert posting["title"]
        assert posting["company"]
        assert isinstance(posting["description_text"], str)
        assert isinstance(posting["employment_type"], str)
        # posted_at is either empty or ISO-8601 with a timezone.
        if posting["posted_at"]:
            assert posting["posted_at"][:4].isdigit()


def test_greenhouse():
    postings = sources.normalize_greenhouse(load("greenhouse.json"), "Netlify")
    assert_shape(postings)
    first = postings[0]
    assert first["source"] == "greenhouse"
    assert first["company"] == "Netlify"
    assert first["location"] == "Remote"
    assert "boards.greenhouse.io" in first["url"] or "greenhouse.io" in first["url"]
    # Greenhouse ships HTML-escaped markup; it must arrive as readable text.
    assert "&lt;" not in first["description_text"]
    assert "<p>" not in first["description_text"]


def test_lever():
    postings = sources.normalize_lever(load("lever.json"), "Palantir")
    assert_shape(postings)
    first = postings[0]
    assert first["source"] == "lever"
    assert first["company"] == "Palantir"
    assert first["title"] == "Administrative Business Partner"
    assert "London" in first["location"]
    assert first["employment_type"]  # categories.commitment
    # createdAt arrives as epoch milliseconds and must become an ISO date.
    assert first["posted_at"].startswith("20")


def test_ashby():
    postings = sources.normalize_ashby(load("ashby.json"), "Railway")
    assert_shape(postings)
    first = postings[0]
    assert first["source"] == "ashby"
    assert first["company"] == "Railway"
    assert first["title"] == "Senior Full-Stack Engineer - Product"
    assert first["location"] == "Global"
    assert first["remote"] is True
    assert first["employment_type"] == "FullTime"


def test_ashby_skips_unlisted_jobs():
    payload = load("ashby.json")
    payload["jobs"][0]["isListed"] = False
    postings = sources.normalize_ashby(payload, "Railway")
    assert all(p["title"] != "Senior Full-Stack Engineer - Product" for p in postings)


def test_remotive():
    postings = sources.normalize_remotive(load("remotive.json"))
    assert_shape(postings)
    first = postings[0]
    assert first["source"] == "remotive"
    assert first["remote"] is True
    assert first["title"] == "Senior React Full-stack Developer"
    assert "<" not in first["description_text"][:200]


def test_remoteok():
    payload = load("remoteok.json")
    assert "legal" in payload[0], "fixture should still contain the legal notice element"
    postings = sources.normalize_remoteok(payload)
    assert_shape(postings)
    # The legal notice is not a job and must be dropped.
    assert len(postings) == len(payload) - 1
    first = postings[0]
    assert first["source"] == "remoteok"
    assert first["remote"] is True
    assert first["company"]


def test_hn_thread_selection_picks_the_newest():
    thread = sources.find_hn_thread(load("hn_search.json"))
    assert thread is not None
    assert thread["author"] == "whoishiring"
    hits = load("hn_search.json")["hits"]
    assert thread["created_at_i"] == max(h["created_at_i"] for h in hits)


def test_hn_comments_become_postings():
    postings = sources.normalize_hn(load("hn_item.json"))
    assert_shape(postings)
    first = postings[0]
    assert first["source"] == "hn"
    # Company is the text before the first pipe on the header line.
    assert first["company"] == "Modash.io"
    assert first["url"].startswith("https://news.ycombinator.com/item?id=")
    assert "|" in first["title"]


def test_hn_skips_empty_comments():
    payload = load("hn_item.json")
    payload["children"].append({"id": 999, "text": None, "author": "dead"})
    postings = sources.normalize_hn(payload)
    assert all(p["url"] != "https://news.ycombinator.com/item?id=999" for p in postings)


@pytest.mark.parametrize("value,expected_prefix", [
    (1756742400000, "20"),      # Lever: epoch milliseconds
    (1756742400, "20"),         # RemoteOK: epoch seconds
    ("2026-09-01T15:01:17Z", "2026-09-01"),
    ("2026-09-01", "2026-09-01"),
    (None, ""),
    ("", ""),
])
def test_timestamp_normalization(value, expected_prefix):
    assert sources._iso(value).startswith(expected_prefix)


def test_app_password_spaces_are_stripped(monkeypatch):
    """Google shows an App Password as "abcd efgh ijkl mnop"; the password
    itself has no spaces, so pasting it as displayed must still authenticate."""
    import mailer
    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, user, password): sent["password"] = password
        def send_message(self, msg): sent["msg"] = msg

    for key, value in {
        "SMTP_HOST": "smtp.example.com", "SMTP_PORT": "587",
        "SMTP_USER": "me@example.com", "SMTP_PASS": "abcd efgh ijkl mnop",
        "EMAIL_TO": "me@example.com",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)

    mailer.send_digest("jobwatch: 1 new match", "# digest\n")
    assert sent["password"] == "abcdefghijklmnop"


def test_a_blank_password_is_reported_as_missing(monkeypatch):
    import mailer
    for key, value in {
        "SMTP_HOST": "smtp.example.com", "SMTP_PORT": "587",
        "SMTP_USER": "me@example.com", "SMTP_PASS": "",
        "EMAIL_TO": "me@example.com",
    }.items():
        monkeypatch.setenv(key, value)
    assert "SMTP_PASS" in mailer.missing_env()


# --- USAJOBS ---------------------------------------------------------------
#
# The fixture is hand-built from the published schema, not captured live: the
# endpoint 401s without a real Authorization-Key. These tests pin the
# normalizer's behaviour, not the API's actual field names.

def test_usajobs():
    postings = sources.normalize_usajobs(load("usajobs.json"))
    assert_shape(postings)
    assert len(postings) == 4
    first = postings[0]
    assert first["source"] == "usajobs"
    assert first["company"] == "Cybersecurity and Infrastructure Security Agency"
    assert first["title"] == "IT Specialist (Customer Support)"
    assert first["location"] == "Anywhere in the U.S. (remote job)"
    assert first["url"] == "https://www.usajobs.gov/job/830000100"
    assert first["employment_type"] == "Full-time, Permanent"
    assert first["posted_at"].startswith("2026-09-01")
    # JobSummary arrives as HTML and must be flattened.
    assert "<p>" not in first["description_text"]
    assert "tier 2 customer support" in first["description_text"].lower()


def test_usajobs_remote_only_when_declared_or_no_duty_station():
    by_title = {p["title"]: p for p in sources.normalize_usajobs(load("usajobs.json"))}
    # RemoteIndicator true
    assert by_title["IT Specialist (Customer Support)"]["remote"] is True
    # No RemoteIndicator, but "Location Negotiable After Selection"
    assert by_title["IT Specialist (Applications Software)"]["remote"] is True
    # RemoteIndicator false and a real duty station
    assert by_title["IT Specialist (Solutions Engineer)"]["remote"] is False
    # RemoteIndicator as the string "false" must not read as truthy
    assert by_title["Supervisory IT Specialist (Customer Support)"]["remote"] is False


def test_usajobs_salary_is_annualized():
    by_title = {p["title"]: p for p in sources.normalize_usajobs(load("usajobs.json"))}
    annual = by_title["IT Specialist (Customer Support)"]
    assert annual["salary_min"] == 103409.0
    assert annual["salary_max"] == 134435.0
    # Per-hour rates are multiplied by the 2087-hour OPM work year so they can
    # be sorted against per-year postings.
    hourly = by_title["IT Specialist (Solutions Engineer)"]
    assert hourly["salary_max"] == 60.00 * 2087
    assert hourly["salary_min"] == 45.50 * 2087
    # No PositionRemuneration at all
    none_published = by_title["Supervisory IT Specialist (Customer Support)"]
    assert none_published["salary_max"] is None


def test_usajobs_who_may_apply_reaches_the_description():
    """The not-open flag reads description_text, so WhoMayApply has to land there."""
    import filters
    by_title = {p["title"]: p for p in sources.normalize_usajobs(load("usajobs.json"))}
    restricted = by_title["IT Specialist (Applications Software)"]
    assert "current federal employees only" in restricted["description_text"].lower()
    flags = filters.compute_flags(restricted["description_text"])
    assert "not-open:current federal employees only" in flags

    internal = by_title["Supervisory IT Specialist (Customer Support)"]
    assert "not-open:internal to agency" in filters.compute_flags(
        internal["description_text"])

    open_role = by_title["IT Specialist (Customer Support)"]
    assert not any(f.startswith("not-open") for f in
                   filters.compute_flags(open_role["description_text"]))


def test_usajobs_headers_require_both_credentials(monkeypatch):
    monkeypatch.delenv("USAJOBS_EMAIL", raising=False)
    monkeypatch.delenv("USAJOBS_KEY", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        sources.usajobs_headers()
    assert "USAJOBS_EMAIL" in str(excinfo.value)
    assert "USAJOBS_KEY" in str(excinfo.value)

    monkeypatch.setenv("USAJOBS_EMAIL", "me@example.com")
    monkeypatch.setenv("USAJOBS_KEY", "abc123")
    headers = sources.usajobs_headers()
    assert headers["Host"] == "data.usajobs.gov"
    assert headers["User-Agent"] == "me@example.com"
    assert headers["Authorization-Key"] == "abc123"


def test_usajobs_handles_an_empty_result():
    assert sources.normalize_usajobs({"SearchResult": {"SearchResultItems": []}}) == []
    assert sources.normalize_usajobs({}) == []


# --- Workday ---------------------------------------------------------------
#
# These fixtures were captured live from bah/wd1/BAH_Jobs, unlike the USAJOBS
# one, so they pin the real response shape.

def test_workday_list():
    postings = sources.normalize_workday(
        load("workday_list.json"), "Booz Allen Hamilton", "bah", "wd1", "BAH_Jobs")
    assert_shape(postings)
    first = postings[0]
    assert first["source"] == "workday"
    assert first["company"] == "Booz Allen Hamilton"
    assert first["title"]
    # The public URL is built from tenant/wd/site plus externalPath.
    assert first["url"].startswith("https://bah.wd1.myworkdayjobs.com/BAH_Jobs/")
    # The list carries no description; it costs a second request per posting.
    assert first["description_text"] == ""


def test_workday_skips_rows_without_a_path():
    payload = {"jobPostings": [{"title": "No path here"}]}
    assert sources.normalize_workday(payload, "X", "bah", "wd1", "BAH_Jobs") == []


@pytest.mark.parametrize("slug,expected", [
    ("bah/wd1/BAH_Jobs", ("bah", "wd1", "BAH_Jobs")),
    ("accenture/wd103/AccentureCareers", ("accenture", "wd103", "AccentureCareers")),
])
def test_workday_slug_parsing(slug, expected):
    assert sources.parse_workday_slug(slug) == expected


@pytest.mark.parametrize("slug", ["bah", "bah/wd1", "", "bah/wd1/site/extra"])
def test_a_bad_workday_slug_is_rejected_with_the_expected_shape(slug):
    with pytest.raises(ValueError) as excinfo:
        sources.parse_workday_slug(slug)
    assert "tenant/wdNN/site" in str(excinfo.value)


def test_workday_relative_posted_dates():
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).date()
    assert sources._workday_posted("Posted Today") == today.isoformat()
    assert sources._workday_posted("Posted 7 Days Ago") == (
        today - dt.timedelta(days=7)).isoformat()
    # "30+ Days Ago" floors at 30; it is approximate by nature.
    assert sources._workday_posted("Posted 30+ Days Ago") == (
        today - dt.timedelta(days=30)).isoformat()
    assert sources._workday_posted("") == ""
    assert sources._workday_posted("Posted recently") == ""


def test_workday_detail_shape():
    info = load("workday_detail.json")["jobPostingInfo"]
    # Mirrors fetch_workday_detail without the network call.
    assert info["title"]
    assert info["location"]
    assert info["startDate"].startswith("20")
    from textutil import html_to_text
    text = html_to_text(info["jobDescription"])
    assert "<p>" not in text and "&lt;" not in text
    assert len(text) > 50
