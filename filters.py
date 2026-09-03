"""Include / exclude / flag rules for job postings.

All matching runs against textutil.normalize'd text (lowercased, whitespace and
unicode-dash collapsed) so that "Senior  Support-Engineer" and
"senior support engineer" behave identically.
"""
from __future__ import annotations

import re
from typing import Iterable

from textutil import normalize

# --- keyword tables ---------------------------------------------------------

INCLUDE_TITLE_KEYWORDS = [
    "support engineer",
    "customer support engineer",
    "technical support engineer",
    "solutions engineer",
    "sales engineer",
    "implementation engineer",
    "forward deployed",
    "deployed engineer",
    "customer engineer",
    "technical account manager",
    "developer support",
    "developer advocate",
    "integration engineer",
    "onboarding engineer",
]
# Deliberately NOT included: "ai engineer" / "applied ai engineer". At these
# companies those titles are senior ML research and modelling roles, not the
# customer-facing engineering this list is for.

EXCLUDE_TITLE_KEYWORDS = [
    "contract",
    "contractor",
    "intern",
    "internship",
    "part-time",
    "part time",
    "staff",
    "principal",
    "director",
    "manager",
    "vp",
    "head of",
]

# "manager" alone is an exclusion, but this exact role is one we want.
MANAGER_EXCEPTIONS = ["technical account manager"]

ONSITE_KEYWORDS = ["hybrid", "on-site", "onsite", "in office", "in-office"]

ELIGIBILITY_PHRASES = ["not eligible", "excluding", "except"]

CLEARANCE_PATTERNS = [
    r"\bclearance\b",
    r"\bts\s*/\s*sci\b",
    r"\bsecret\b",
    r"\bdod\b",
    r"\bdepartment of defense\b",
]

US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
]

# Two or more distinct state names in a description usually means a
# hire-in-these-states-only list, which Montana is routinely missing from.
STATE_LIST_THRESHOLD = 2

REMOTE_HINTS = ["remote", "work from home", "wfh", "distributed", "anywhere"]


def _phrase_re(phrase: str) -> "re.Pattern[str]":
    """Word-boundary regex for a phrase, tolerant of space/hyphen differences.

    Guards against "intern" matching "internal" and "vp" matching "vpc".
    """
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", phrase) if p]
    return re.compile(r"\b" + r"[\s\-]+".join(parts) + r"\b")


_INCLUDE_RES = {k: _phrase_re(k) for k in INCLUDE_TITLE_KEYWORDS}
_EXCLUDE_RES = {k: _phrase_re(k) for k in EXCLUDE_TITLE_KEYWORDS}
_ONSITE_RES = {k: _phrase_re(k) for k in ONSITE_KEYWORDS}
_ELIGIBILITY_RES = {k: _phrase_re(k) for k in ELIGIBILITY_PHRASES}
_CLEARANCE_RES = [re.compile(p) for p in CLEARANCE_PATTERNS]
_STATE_RES = {s: _phrase_re(s) for s in US_STATES}
_REMOTE_RE = _phrase_re("remote")
_REMOTE_RES = [_phrase_re(k) for k in REMOTE_HINTS]
_EXCEPTION_RES = [_phrase_re(e) for e in MANAGER_EXCEPTIONS]

# Longest first so "applied ai engineer" wins over "ai engineer" and
# "technical support engineer" wins over "support engineer".
_INCLUDE_ORDER = sorted(INCLUDE_TITLE_KEYWORDS, key=len, reverse=True)


def match_include(text: str) -> "str | None":
    """Return the most specific include keyword present in text, else None."""
    norm = normalize(text)
    for kw in _INCLUDE_ORDER:
        if _INCLUDE_RES[kw].search(norm):
            return kw
    return None


def match_exclude(title: str, employment_type: str = "") -> "str | None":
    """Return the exclusion keyword hit in title/employment_type, else None."""
    norm = normalize(title + " " + (employment_type or ""))
    for kw in EXCLUDE_TITLE_KEYWORDS:
        if not _EXCLUDE_RES[kw].search(norm):
            continue
        if kw == "manager" and any(rx.search(norm) for rx in _EXCEPTION_RES):
            continue
        return kw
    return None


def match_onsite(location: str, description: str) -> "str | None":
    """Return an on-site keyword if present and "remote" is absent, else None.

    "Hybrid - remote 3 days" keeps the posting; a bare "Hybrid, Austin TX"
    drops it.
    """
    norm = normalize((location or "") + " " + (description or ""))
    hit = next((kw for kw in ONSITE_KEYWORDS if _ONSITE_RES[kw].search(norm)), None)
    if not hit:
        return None
    if _REMOTE_RE.search(norm):
        return None
    return hit


def looks_remote(location: str, description: str, declared=None) -> bool:
    if declared is not None:
        return bool(declared)
    norm = normalize((location or "") + " " + (description or ""))
    return any(rx.search(norm) for rx in _REMOTE_RES)


def find_states(description: str) -> "list[str]":
    norm = normalize(description)
    return [s for s, rx in _STATE_RES.items() if rx.search(norm)]


def compute_flags(description: str) -> "list[str]":
    """Flags to eyeball by hand: state eligibility (Montana) and clearance."""
    norm = normalize(description)
    flags = []

    phrase_hits = [p for p in ELIGIBILITY_PHRASES if _ELIGIBILITY_RES[p].search(norm)]
    states = find_states(description)
    if phrase_hits:
        flags.append("eligibility:" + ",".join(phrase_hits))
    if len(states) >= STATE_LIST_THRESHOLD:
        ordered = sorted(states)
        shown = ",".join(ordered[:6])
        more = "" if len(ordered) <= 6 else ",+" + str(len(ordered) - 6)
        flags.append("states:" + shown + more)

    hits = [rx.pattern for rx in _CLEARANCE_RES if rx.search(norm)]
    if hits:
        pretty = []
        if any("clearance" in p for p in hits):
            pretty.append("clearance")
        if any("sci" in p for p in hits):
            pretty.append("ts/sci")
        if any("secret" in p for p in hits):
            pretty.append("secret")
        if any("dod" in p or "defense" in p for p in hits):
            pretty.append("dod")
        flags.append("clearance:" + ",".join(dict.fromkeys(pretty)))

    return flags


# --- tiering ----------------------------------------------------------------
#
# Tier 1  core target: support / solutions / implementation / TAM, no seniority
# Tier 2  adjacent:    forward deployed (FDE) and customer engineer
# Tier 3  everything else, including a tier-1 role carrying a seniority word
#
# The digest sorted by tier reads top-down in the order worth applying in.

TIER1_KEYWORDS = ["support", "solutions", "implementation", "technical account manager"]

TIER2_KEYWORDS = ["forward deployed", "deployed engineer", "customer engineer"]

# "II" is deliberately absent - "Support Engineer II" is a mid-level role, not a
# senior one. staff/principal/director/head/vp never reach here (they are
# excluded outright); they are listed so the rule stands on its own.
SENIORITY_WORDS = [
    "senior", "sr", "lead", "chief", "distinguished", "advanced",
    "staff", "principal", "director", "head", "vp", "iii", "iv",
]

_TIER1_RES = [_phrase_re(k) for k in TIER1_KEYWORDS]
_TIER2_RES = [_phrase_re(k) for k in TIER2_KEYWORDS]
_SENIORITY_RES = {w: _phrase_re(w) for w in SENIORITY_WORDS}

TIER_LABELS = {1: "Tier 1 - core target", 2: "Tier 2 - adjacent", 3: "Tier 3 - everything else"}


def match_seniority(title: str) -> "str | None":
    norm = normalize(title)
    for word in SENIORITY_WORDS:
        if _SENIORITY_RES[word].search(norm):
            return word
    return None


def compute_tier(title: str) -> int:
    """1, 2 or 3. Tier 1 requires a core keyword AND no seniority word."""
    norm = normalize(title)
    if any(rx.search(norm) for rx in _TIER1_RES) and not match_seniority(title):
        return 1
    if any(rx.search(norm) for rx in _TIER2_RES):
        return 2
    return 3


def evaluate(posting: dict, match_description: bool = False) -> "dict | None":
    """Apply all rules to a normalized posting.

    Returns a copy of the posting augmented with matched_keyword, matched_in
    and flags if it survives, or None if it was excluded. reject_reason is set
    on the input dict either way, for logging.
    """
    title = posting.get("title") or ""
    description = posting.get("description_text") or ""
    location = posting.get("location") or ""
    employment_type = posting.get("employment_type") or ""

    keyword = match_include(title)
    matched_in = "title" if keyword else None
    if not keyword and match_description:
        keyword = match_include(description)
        matched_in = "description" if keyword else None
    if not keyword:
        posting["reject_reason"] = "no include keyword"
        return None

    excluded = match_exclude(title, employment_type)
    if excluded:
        posting["reject_reason"] = "excluded term: " + excluded
        return None

    onsite = match_onsite(location, description)
    if onsite:
        posting["reject_reason"] = "on-site term without remote: " + onsite
        return None

    result = dict(posting)
    result["matched_keyword"] = keyword
    result["matched_in"] = matched_in
    result["flags"] = compute_flags(description)
    result["tier"] = compute_tier(title)
    result["remote"] = looks_remote(location, description, posting.get("remote"))
    result.pop("reject_reason", None)
    return result


def evaluate_all(postings: "Iterable[dict]", match_description: bool = False) -> "list[dict]":
    kept = []
    for posting in postings:
        result = evaluate(posting, match_description=match_description)
        if result:
            kept.append(result)
    return kept
