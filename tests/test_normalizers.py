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
