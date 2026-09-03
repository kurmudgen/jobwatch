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
    "supervisory",
]

# "manager" alone is an exclusion, but this exact role is one we want.
MANAGER_EXCEPTIONS = ["technical account manager"]

ONSITE_KEYWORDS = ["hybrid", "on-site", "onsite", "in office", "in-office"]

ELIGIBILITY_PHRASES = ["not eligible", "excluding", "except"]

# Federal postings that are closed to the public. USAJOBS puts this in
# WhoMayApply, which the normalizer folds into description_text.
NOT_OPEN_PHRASES = ["current federal employees only", "internal to agency"]

# "secret" on its own is far too loose - "our globally distributed team is our
# secret weapon" was flagging every Supabase support role as needing a security
# clearance. Require it to actually sit in a clearance context.
CLEARANCE_PATTERNS = [
    r"\bclearance\b",
    r"\bts\s*/\s*sci\b",
    r"\btop[\s\-]secret\b",
    r"\bsecret\b(?=[\s\w]{0,30}\bclearance\b)",
    r"\bclearance\b(?=[\s\w]{0,30}\bsecret\b)",
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
_NOT_OPEN_RES = {k: _phrase_re(k) for k in NOT_OPEN_PHRASES}
_CLEARANCE_RES = [re.compile(p) for p in CLEARANCE_PATTERNS]
_STATE_RES = {s: _phrase_re(s) for s in US_STATES}
_REMOTE_RE = _phrase_re("remote")
_REMOTE_RES = [_phrase_re(k) for k in REMOTE_HINTS]
_EXCEPTION_RES = [_phrase_re(e) for e in MANAGER_EXCEPTIONS]

# Longest first so "technical support engineer" wins over "support engineer"
# and "customer support engineer" over both.
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


# A bare "remote" somewhere in a long description is not evidence that THIS role
# is remote. Palantir's postings carry the boilerplate "there are a few roles
# that allow for 'Remote' work on an exceptional basis", which was rescuing
# roles whose location literally reads "(onsite)". These phrases are about the
# role itself, so they still override an on-site location tag - which is how
# Vercel's "Hybrid - London, Berlin" but "this role is remote-first" survives.
# Every entry must be at least two words. A single-word phrase, or one whose
# non-word characters get stripped by _phrase_re, collapses to a bare
# \bremote\b and silently rescues everything - which is exactly the bug this
# list exists to prevent.
STRONG_REMOTE_PHRASES = [
    "remote first", "fully remote", "100% remote", "remote friendly",
    "work from anywhere", "remote position", "remote role", "remote based",
    "remote opportunity", "this role is remote",
]
_STRONG_REMOTE_RES = [_phrase_re(p) for p in STRONG_REMOTE_PHRASES]
assert all(len(p.split()) >= 2 for p in STRONG_REMOTE_PHRASES), \
    "a one-word strong-remote phrase would match any bare 'remote'"


def _has_strong_remote(text: str) -> bool:
    norm = normalize(text)
    return any(rx.search(norm) for rx in _STRONG_REMOTE_RES)


def match_onsite(location: str, description: str) -> "str | None":
    """Return an on-site keyword if present and not overridden by remote.

    The "unless it also contains remote" escape hatch is applied per field. An
    on-site term in the LOCATION is decisive unless the location itself says
    remote or the description makes a role-level remote claim; a bare "remote"
    buried in description boilerplate does not rescue an "(onsite)" location.
    """
    location = location or ""
    description = description or ""

    loc_norm = normalize(location)
    loc_hit = next((kw for kw in ONSITE_KEYWORDS if _ONSITE_RES[kw].search(loc_norm)), None)
    if loc_hit:
        if location_says_remote(location) or _has_strong_remote(description):
            return None
        return loc_hit

    desc_norm = normalize(description)
    desc_hit = next((kw for kw in ONSITE_KEYWORDS if _ONSITE_RES[kw].search(desc_norm)), None)
    if desc_hit and not _REMOTE_RE.search(desc_norm):
        return desc_hit
    return None


# --- US-remote filter -------------------------------------------------------
#
# Keep a posting only if its LOCATION says remote and points at the US (or names
# no country at all). Descriptions are not consulted: a description mentioning
# the US proves nothing about where the role is based.

# Unambiguous "this is a US role" tokens. Checked before the non-US list, so a
# multi-region posting like "Remote - NA, APAC, EMEA" is kept.
STRONG_US_MARKERS = [
    "united states", "usa", "u s", "us", "north america", "na",
    "amer", "americas", "conus", "nationwide",
]

# Weaker: a state name or abbreviation. Checked *after* the non-US list, so
# "CA-Ontario-Toronto" reads as Canada rather than California.
US_STATE_ABBREVS = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
]

NON_US_REGIONS = [
    "emea", "apac", "latam", "anz", "europe", "european", "asia", "africa",
    "middle east", "oceania", "eu", "uk", "gb", "benelux", "dach", "nordics",
]

NON_US_COUNTRIES = [
    "canada", "mexico", "brazil", "argentina", "chile", "peru", "colombia",
    "united kingdom", "england", "scotland", "ireland", "france", "germany",
    "spain", "italy", "netherlands", "belgium", "poland", "sweden", "norway",
    "denmark", "finland", "iceland", "portugal", "austria", "switzerland",
    "czechia", "czech republic", "romania", "bulgaria", "hungary", "greece",
    "croatia", "serbia", "slovakia", "slovenia", "ukraine", "lithuania",
    "latvia", "estonia", "luxembourg", "malta", "cyprus", "turkey", "israel",
    "uae", "united arab emirates", "saudi arabia", "qatar", "kuwait", "egypt",
    "morocco", "tunisia", "south africa", "nigeria", "kenya", "ghana",
    "india", "pakistan", "bangladesh", "sri lanka", "nepal", "china", "japan",
    "south korea", "korea", "singapore", "taiwan", "hong kong", "malaysia",
    "indonesia", "thailand", "vietnam", "philippines", "australia",
    "new zealand", "russia", "kazakhstan", "armenia",
]

NON_US_CITIES = [
    "london", "dublin", "berlin", "munich", "hamburg", "paris", "amsterdam",
    "rotterdam", "brussels", "madrid", "barcelona", "lisbon", "milan", "rome",
    "zurich", "geneva", "vienna", "stockholm", "oslo", "copenhagen",
    "helsinki", "warsaw", "krakow", "wroclaw", "gdansk", "prague", "budapest",
    "bucharest", "sofia", "athens", "istanbul", "tel aviv", "dubai",
    "abu dhabi", "riyadh", "doha", "cairo", "lagos", "nairobi",
    "johannesburg", "cape town", "bangalore", "bengaluru", "mumbai", "delhi",
    "hyderabad", "chennai", "pune", "beijing", "shanghai", "shenzhen",
    "tokyo", "osaka", "kyoto", "seoul", "taipei", "kuala lumpur", "jakarta",
    "bangkok", "manila", "hanoi", "sydney", "melbourne", "brisbane", "perth",
    "auckland", "wellington", "toronto", "vancouver", "montreal", "ottawa",
    "calgary", "ontario", "quebec", "guadalajara", "sao paulo",
    "rio de janeiro", "buenos aires", "santiago", "lima", "bogota", "vilnius",
    "riga", "tallinn",
]

_STRONG_US_RES = [_phrase_re(m) for m in STRONG_US_MARKERS]
_US_STATE_NAME_RES = [_phrase_re(s) for s in US_STATES]
# Abbreviations are matched case-sensitively against the raw location so the
# English words "in", "or", "ok", "me", "hi" cannot be read as state codes.
_US_ABBREV_RE = re.compile(r"\b(" + "|".join(US_STATE_ABBREVS) + r")\b")
_NON_US_RES = [
    _phrase_re(m) for m in NON_US_REGIONS + NON_US_COUNTRIES + NON_US_CITIES
]


def has_us_marker(location: str) -> bool:
    """True if the location names the US, a US state, or a US-wide region."""
    norm = normalize(location)
    if any(rx.search(norm) for rx in _STRONG_US_RES):
        return True
    if any(rx.search(norm) for rx in _US_STATE_NAME_RES):
        return True
    return bool(_US_ABBREV_RE.search(location or ""))


def has_non_us_marker(location: str) -> bool:
    norm = normalize(location)
    return any(rx.search(norm) for rx in _NON_US_RES)


# USAJOBS never writes "remote" in the location for a role with no duty
# station; it writes one of these instead. Both mean US-wide remote.
REMOTE_LOCATION_PHRASES = [
    "remote",
    "location negotiable",
    "anywhere in the us",
    "anywhere in the united states",
]
_REMOTE_LOCATION_RES = [_phrase_re(p) for p in REMOTE_LOCATION_PHRASES]


def location_says_remote(location: str) -> bool:
    """Periods are stripped so "Anywhere in the U.S." reads as "anywhere in the us"."""
    norm = normalize(location).replace(".", "")
    return any(rx.search(norm) for rx in _REMOTE_LOCATION_RES)


def is_us_remote(location: str) -> bool:
    """Remote, and either US-flavoured or naming no country at all."""
    location = location or ""
    norm = normalize(location)
    if not location_says_remote(location):
        return False

    # Strong US tokens win outright, so a multi-region posting that includes
    # North America survives alongside EMEA and APAC. Periods stripped so
    # "U.S." and "U.S.A." register.
    nodots = norm.replace(".", "")
    if any(rx.search(nodots) for rx in _STRONG_US_RES):
        return True
    # An explicit non-US country, city or region drops it.
    if has_non_us_marker(location):
        return False
    # A state name or code is a US signal once no foreign place is present.
    if any(rx.search(norm) for rx in _US_STATE_NAME_RES):
        return True
    if _US_ABBREV_RE.search(location):
        return True
    # Remote with no country named at all ("Remote", "Remote, Global").
    return True


def filter_us_remote(postings: "Iterable[dict]") -> "list[dict]":
    return [p for p in postings if is_us_remote(p.get("location") or "")]


# --- dedupe -----------------------------------------------------------------

def _dedupe_score(posting: dict) -> tuple:
    """Higher sorts first: prefer the US/AMER copy of a cloned req."""
    location = posting.get("location") or ""
    return (
        1 if has_us_marker(location) else 0,
        1 if _REMOTE_RE.search(normalize(location)) else 0,
        -len(location),  # a shorter location is usually the cleaner listing
    )


def dedupe(postings: "Iterable[dict]") -> "list[dict]":
    """Collapse reqs that share company + title and differ only by location,
    keeping the US/AMER one. Order is otherwise preserved."""
    groups: "dict[tuple, list[dict]]" = {}
    order = []
    for posting in postings:
        key = (
            normalize(posting.get("company") or ""),
            normalize(posting.get("title") or ""),
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(posting)

    kept = []
    for key in order:
        candidates = groups[key]
        if len(candidates) == 1:
            kept.append(candidates[0])
        else:
            kept.append(max(candidates, key=_dedupe_score))
    return kept


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

    not_open = [p for p in NOT_OPEN_PHRASES if _NOT_OPEN_RES[p].search(norm)]
    if not_open:
        flags.append("not-open:" + ",".join(not_open))

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


# --- federal (USAJOBS) include gate ----------------------------------------
#
# Federal 2210 titles are "IT Specialist (CUSTSPT)", "IT Specialist (APPSW)",
# "IT Cybersecurity Specialist (INFOSEC)" and so on. None of them contain
# "engineer", so INCLUDE_TITLE_KEYWORDS - which is built around private-sector
# engineer titles - matches almost none of them and would silently discard the
# entire source. For USAJOBS the API query is the include gate instead:
# JobCategoryCode=2210, one of four keywords, RemoteIndicator=True and
# PayGradeLow=11 together are already a tighter filter than a title regex.
# Exclusions, the on-site rule and every flag still apply.

FEDERAL_SOURCE = "usajobs"

USAJOBS_QUERY_KEYWORDS = ["solutions", "customer support", "applications", "cybersecurity"]
USAJOBS_FALLBACK_KEYWORD = "it specialist (2210)"

_USAJOBS_KEYWORD_RES = {k: _phrase_re(k) for k in USAJOBS_QUERY_KEYWORDS}
# Longest first so "customer support" wins over a bare "support".
_USAJOBS_KEYWORD_ORDER = sorted(USAJOBS_QUERY_KEYWORDS, key=len, reverse=True)


def usajobs_keyword(title: str) -> str:
    """Which of the four searched keywords the title shows, else the category."""
    norm = normalize(title)
    for keyword in _USAJOBS_KEYWORD_ORDER:
        if _USAJOBS_KEYWORD_RES[keyword].search(norm):
            return keyword
    return USAJOBS_FALLBACK_KEYWORD


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

    is_federal = posting.get("source") == FEDERAL_SOURCE

    keyword = match_include(title)
    matched_in = "title" if keyword else None
    if not keyword and match_description:
        keyword = match_include(description)
        matched_in = "description" if keyword else None
    if not keyword and is_federal:
        # The USAJOBS query already restricted this to remote 2210 roles at
        # GS-11 and above matching one of the four keywords.
        keyword = usajobs_keyword(title)
        matched_in = "usajobs query"
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
