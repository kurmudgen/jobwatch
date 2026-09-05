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
# The fixture is CAPTURED LIVE from data.usajobs.gov (2026-09-03), so these
# pin the real response shape. Each item carries a _fixture_case label:
# remote/negotiable, not-open, clearance, hourly-or-plain.

def usajobs_rows():
    payload = load("usajobs.json")
    labels = [i["_fixture_case"] for i in payload["SearchResult"]["SearchResultItems"]]
    return dict(zip(labels, sources.normalize_usajobs(payload)))


def test_usajobs_shape():
    postings = sources.normalize_usajobs(load("usajobs.json"))
    assert_shape(postings)
    assert len(postings) == 4
    for posting in postings:
        assert posting["source"] == "usajobs"
        assert posting["company"]
        assert posting["url"].startswith("https://www.usajobs.gov/job/")


def test_usajobs_strips_the_port_from_position_uri():
    """Live PositionURI values come back as "https://www.usajobs.gov:443/job/1",
    which would key the same posting twice if the format ever changed."""
    raw = load("usajobs.json")["SearchResult"]["SearchResultItems"][0]
    assert ":443" in raw["MatchedObjectDescriptor"]["PositionURI"]
    assert all(":443" not in p["url"] for p in sources.normalize_usajobs(load("usajobs.json")))


def test_location_negotiable_is_not_remote_without_the_indicator():
    """In federal HR "Location Negotiable After Selection" means the duty
    station is chosen after selection, not that the role is remote. Every one
    of these measured live was RemoteIndicator=False, TeleworkEligible=True."""
    rows = usajobs_rows()
    negotiable = rows["remote/negotiable"]
    assert "Negotiable" in negotiable["location"]
    assert negotiable["remote"] is False
    assert rows["not-open"]["remote"] is False
    assert rows["hourly-or-plain"]["remote"] is False


def test_location_negotiable_is_remote_when_the_indicator_agrees():
    payload = load("usajobs.json")
    item = payload["SearchResult"]["SearchResultItems"][0]
    item["MatchedObjectDescriptor"]["UserArea"]["Details"]["RemoteIndicator"] = True
    row = sources.normalize_usajobs(payload)[0]
    assert row["remote"] is True
    # The location is annotated so the location-only us-remote filter agrees.
    assert "(remote)" in row["location"]
    import filters
    assert filters.is_us_remote(row["location"]) is True


def test_anywhere_in_the_us_is_remote_on_its_face():
    payload = load("usajobs.json")
    d = payload["SearchResult"]["SearchResultItems"][0]["MatchedObjectDescriptor"]
    d["PositionLocationDisplay"] = "Anywhere in the U.S. (remote job)"
    d["UserArea"]["Details"]["RemoteIndicator"] = False
    assert sources.normalize_usajobs(payload)[0]["remote"] is True


def test_telework_eligible_is_not_treated_as_remote():
    payload = load("usajobs.json")
    det = payload["SearchResult"]["SearchResultItems"][0]["MatchedObjectDescriptor"]["UserArea"]["Details"]
    det["RemoteIndicator"] = False
    det["TeleworkEligible"] = True
    row = sources.normalize_usajobs(payload)[0]
    assert row["remote"] is False
    # But it is recorded, because it is worth knowing.
    assert "Telework eligible: yes" in row["description_text"]
    assert "RemoteIndicator: no" in row["description_text"]


@pytest.mark.parametrize("raw,expected", [
    (True, True), (False, False), ("true", True), ("True", True),
    ("false", False), ("no", False), (None, None), ("maybe", None),
])
def test_usajobs_boolean_parsing(raw, expected):
    assert sources._usajobs_flag({"X": raw}, "X") is expected
    assert sources._usajobs_flag({}, "X") is None


def test_usajobs_salary_is_parsed_and_annualized():
    rows = usajobs_rows()
    for row in rows.values():
        assert row["salary_max"] is None or row["salary_max"] > 0
    negotiable = rows["remote/negotiable"]
    assert negotiable["salary_min"] and negotiable["salary_max"]
    assert negotiable["salary_max"] > negotiable["salary_min"]
    # Per-hour rates scale by the 2087-hour OPM work year.
    assert sources._RATE_TO_ANNUAL["PH"] == 2087.0
    assert sources._RATE_TO_ANNUAL["PA"] == 1.0


def test_usajobs_eligibility_comes_from_hiring_path_not_who_may_apply():
    """Measured across 100 live records, WhoMayApply.Name was empty on all of
    them. HiringPathDisplay is the field that actually carries eligibility."""
    import filters
    rows = usajobs_rows()
    restricted = rows["not-open"]
    assert "not open to the public" in restricted["description_text"].lower()
    assert "not-open:not open to the public" in filters.compute_flags(
        restricted["description_text"])
    # A public posting must not be flagged.
    assert not any(f.startswith("not-open") for f in
                   filters.compute_flags(rows["remote/negotiable"]["description_text"]))


def test_usajobs_clearance_comes_from_the_structured_field():
    import filters
    rows = usajobs_rows()
    assert "security clearance required" in rows["clearance"]["description_text"].lower()
    assert any(f.startswith("clearance:") for f in
               filters.compute_flags(rows["clearance"]["description_text"]))


def test_usajobs_not_required_clearance_does_not_flag():
    assert sources._usajobs_clearance({"SecurityClearance": "Not Required"}) == ""
    assert sources._usajobs_clearance({}) == ""
    assert "Secret" in sources._usajobs_clearance({"SecurityClearance": "Secret"})


def test_usajobs_eligibility_wording():
    public = sources._usajobs_eligibility({"HiringPathDisplay": ["Open to the public"]})
    assert "not open to the public" not in public.lower()
    restricted = sources._usajobs_eligibility(
        {"HiringPathDisplay": ["Competitive service", "Veterans"]})
    assert restricted.startswith("Not open to the public.")
    assert sources._usajobs_eligibility({}) == ""


def test_usajobs_does_not_send_remote_indicator_by_default():
    """RemoteIndicator=True combined with JobCategoryCode=2210 returned exactly
    zero rows on the live API; the location rule does the work instead."""
    assert sources.USAJOBS_SEND_REMOTE_INDICATOR is False


def test_usajobs_descriptions_are_flattened():
    for row in sources.normalize_usajobs(load("usajobs.json")):
        assert "<p>" not in row["description_text"]
        assert "&lt;" not in row["description_text"]


def test_usajobs_timestamps_with_four_digit_fractional_seconds():
    """Live PublicationStartDate looks like "2026-02-06T00:00:00.0000"."""
    assert sources._iso("2026-02-06T00:00:00.0000").startswith("2026-02-06")
    assert sources._iso("2026-02-06T00:00:00.0000000").startswith("2026-02-06")


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


def test_greenhouse_folds_offices_into_the_location():
    """Greenhouse splits location across two fields and they disagree. Verkada's
    Northeast SLED role has location "Boston, MA United States" but offices
    ["Massachusetts Remote", "New York Remote"], and the board's own job-seeker
    feed lists it as Remote. Reading location alone drops it."""
    import filters
    payload = {"jobs": [{
        "title": "Enterprise Solutions Engineer, Northeast SLED",
        "location": {"name": "Boston, MA United States"},
        "offices": [{"name": "Massachusetts Remote"}, {"name": "New York Remote"}],
        "absolute_url": "https://example.com/1",
        "content": "",
    }]}
    row = sources.normalize_greenhouse(payload, "Verkada")[0]
    assert "Massachusetts Remote" in row["location"]
    assert "Boston, MA United States" in row["location"]
    assert filters.is_us_remote(row["location"]) is True


def test_greenhouse_does_not_repeat_an_office_already_in_the_location():
    payload = {"jobs": [{
        "title": "Support Engineer",
        "location": {"name": "Remote - US"},
        "offices": [{"name": "Remote - US"}],
        "absolute_url": "https://example.com/2", "content": "",
    }]}
    assert sources.normalize_greenhouse(payload, "X")[0]["location"] == "Remote - US"


def test_greenhouse_handles_missing_or_empty_offices():
    for offices in ([], None, [{"name": ""}], [None]):
        payload = {"jobs": [{"title": "Support Engineer",
                             "location": {"name": "Austin, TX"},
                             "offices": offices,
                             "absolute_url": "https://example.com/3", "content": ""}]}
        assert sources.normalize_greenhouse(payload, "X")[0]["location"] == "Austin, TX"


def test_greenhouse_uses_offices_when_there_is_no_location():
    payload = {"jobs": [{"title": "Support Engineer", "location": None,
                         "offices": [{"name": "California Remote"}],
                         "absolute_url": "https://example.com/4", "content": ""}]}
    assert sources.normalize_greenhouse(payload, "X")[0]["location"] == "California Remote"
