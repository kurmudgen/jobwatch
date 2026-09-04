"""Fetch and normalize postings from every supported source.

Every fetcher returns a list of dicts with exactly these keys:

    source, company, title, location, remote, employment_type,
    url, posted_at, description_text, salary_min, salary_max

Network failures are the caller's problem to log; the `fetch_*` helpers raise
and `collect()` isolates each source behind its own try/except so one dead
endpoint never kills a run.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import time

import requests
from urllib.parse import urlencode

from textutil import first_line, html_to_text

log = logging.getLogger("jobwatch.sources")

TIMEOUT = 20
RETRIES = 1  # one retry after the first attempt
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

FIELDS = (
    "source", "company", "title", "location", "remote",
    "employment_type", "url", "posted_at", "description_text",
    # Only USAJOBS publishes a salary range; None everywhere else.
    "salary_min", "salary_max",
)


def _get_json(url: str, headers: "dict | None" = None):
    """GET with a 20s timeout and one retry. Raises on final failure."""
    last_error = None
    for attempt in range(RETRIES + 1):
        try:
            response = requests.get(
                url, timeout=TIMEOUT, headers=headers or {"User-Agent": BROWSER_UA}
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - reported by the caller
            last_error = exc
            if attempt < RETRIES:
                log.debug("retrying %s after %s", url, exc)
                time.sleep(1.5)
    raise last_error


def _iso(value) -> str:
    """Best-effort conversion of whatever a source calls a timestamp to ISO-8601."""
    if value in (None, "", 0):
        return ""
    if isinstance(value, (int, float)):
        # Lever and RemoteOK use epoch; Lever is milliseconds.
        seconds = value / 1000.0 if value > 1e11 else float(value)
        try:
            return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    text = str(value).strip()
    if not text:
        return ""
    cleaned = text.replace("Z", "+00:00")
    for parser in (
        lambda s: dt.datetime.fromisoformat(s),
        lambda s: dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S"),
        lambda s: dt.datetime.strptime(s, "%Y-%m-%d"),
    ):
        try:
            parsed = parser(cleaned)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.isoformat()
        except (ValueError, TypeError):
            continue
    return text


def _posting(**kwargs) -> dict:
    """Build a normalized posting, filling in every field."""
    row = {field: kwargs.get(field) for field in FIELDS}
    row["title"] = (row["title"] or "").strip()
    row["company"] = (row["company"] or "").strip()
    row["location"] = (row["location"] or "").strip()
    row["employment_type"] = (row["employment_type"] or "").strip()
    row["url"] = (row["url"] or "").strip()
    row["posted_at"] = _iso(row["posted_at"])
    row["description_text"] = row["description_text"] or ""
    return row


# --- Greenhouse -------------------------------------------------------------

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def normalize_greenhouse(payload: dict, company: str) -> "list[dict]":
    out = []
    for job in payload.get("jobs") or []:
        location = ((job.get("location") or {}).get("name")) or ""
        description = html_to_text(job.get("content"))
        metadata = {
            (m.get("name") or "").lower(): m.get("value")
            for m in (job.get("metadata") or [])
            if isinstance(m, dict)
        }
        employment_type = metadata.get("employment type") or metadata.get("job type") or ""
        if isinstance(employment_type, list):
            employment_type = ", ".join(str(v) for v in employment_type)
        out.append(_posting(
            source="greenhouse",
            company=company,
            title=job.get("title"),
            location=location,
            remote=None,
            employment_type=employment_type,
            url=job.get("absolute_url"),
            posted_at=job.get("first_published") or job.get("updated_at"),
            description_text=description,
        ))
    return out


def fetch_greenhouse(slug: str, company: str) -> "list[dict]":
    return normalize_greenhouse(_get_json(GREENHOUSE_URL.format(slug=slug)), company)


# --- Lever ------------------------------------------------------------------

LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"


def normalize_lever(payload: list, company: str) -> "list[dict]":
    out = []
    for job in payload or []:
        categories = job.get("categories") or {}
        description = job.get("descriptionPlain") or html_to_text(job.get("description"))
        lists = job.get("lists") or []
        extra = "\n".join(
            html_to_text((item.get("text") or "") + " " + (item.get("content") or ""))
            for item in lists
            if isinstance(item, dict)
        )
        closing = job.get("additionalPlain") or html_to_text(job.get("additional"))
        full = "\n\n".join(part for part in (description, extra, closing) if part)
        workplace = job.get("workplaceType") or ""
        location = categories.get("location") or ""
        if workplace and workplace.lower() not in location.lower():
            location = (location + " (" + workplace + ")").strip()
        out.append(_posting(
            source="lever",
            company=company,
            title=job.get("text"),
            location=location,
            remote=True if workplace.lower() == "remote" else None,
            employment_type=categories.get("commitment") or "",
            url=job.get("hostedUrl") or job.get("applyUrl"),
            posted_at=job.get("createdAt") or job.get("updatedAt"),
            description_text=full,
        ))
    return out


def fetch_lever(slug: str, company: str) -> "list[dict]":
    return normalize_lever(_get_json(LEVER_URL.format(slug=slug)), company)


# --- Ashby ------------------------------------------------------------------

ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def normalize_ashby(payload: dict, company: str) -> "list[dict]":
    out = []
    for job in payload.get("jobs") or []:
        if job.get("isListed") is False:
            continue
        locations = [job.get("location") or ""]
        for secondary in job.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                locations.append(secondary.get("location") or "")
            else:
                locations.append(str(secondary))
        location = ", ".join(dict.fromkeys(loc for loc in locations if loc))
        description = job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml"))
        out.append(_posting(
            source="ashby",
            company=company,
            title=job.get("title"),
            location=location,
            remote=job.get("isRemote"),
            employment_type=job.get("employmentType") or "",
            url=job.get("jobUrl") or job.get("applyUrl"),
            posted_at=job.get("publishedAt") or job.get("updatedAt"),
            description_text=description,
        ))
    return out


def fetch_ashby(slug: str, company: str) -> "list[dict]":
    return normalize_ashby(_get_json(ASHBY_URL.format(slug=slug)), company)


# --- Remotive ---------------------------------------------------------------

REMOTIVE_URL = "https://remotive.com/api/remote-jobs?category=software-dev"


def normalize_remotive(payload: dict) -> "list[dict]":
    out = []
    for job in payload.get("jobs") or []:
        out.append(_posting(
            source="remotive",
            company=job.get("company_name"),
            title=job.get("title"),
            location=job.get("candidate_required_location") or "Remote",
            remote=True,
            employment_type=job.get("job_type") or "",
            url=job.get("url"),
            posted_at=job.get("publication_date"),
            description_text=html_to_text(job.get("description")),
        ))
    return out


def fetch_remotive() -> "list[dict]":
    return normalize_remotive(_get_json(REMOTIVE_URL))


# --- RemoteOK ---------------------------------------------------------------

REMOTEOK_URL = "https://remoteok.com/api"


def normalize_remoteok(payload: list) -> "list[dict]":
    out = []
    for job in payload or []:
        if not isinstance(job, dict):
            continue
        # The first element of the feed is a legal notice, not a job.
        if job.get("legal") or not job.get("position"):
            continue
        tags = job.get("tags") or []
        employment_type = ""
        for tag in tags:
            if str(tag).lower() in ("full-time", "part-time", "contract", "freelance"):
                employment_type = str(tag)
                break
        out.append(_posting(
            source="remoteok",
            company=job.get("company"),
            title=job.get("position"),
            location=job.get("location") or "Remote",
            remote=True,
            employment_type=employment_type,
            url=job.get("url") or job.get("apply_url"),
            posted_at=job.get("date") or job.get("epoch"),
            description_text=html_to_text(job.get("description")),
        ))
    return out


def fetch_remoteok() -> "list[dict]":
    return normalize_remoteok(_get_json(REMOTEOK_URL, headers={"User-Agent": BROWSER_UA}))


# --- Hacker News "Who is hiring" -------------------------------------------

# The relevance-ranked /search endpoint returns 20 arbitrary hits - as of this
# writing its newest "Who is hiring" story is from 2020 - so ask the date-sorted
# endpoint first and keep the relevance one only as a fallback.
HN_SEARCH_BY_DATE_URL = (
    'https://hn.algolia.com/api/v1/search_by_date?query="Ask HN: Who is hiring"'
    "&tags=story,author_whoishiring&hitsPerPage=10"
)
HN_SEARCH_URL = (
    'https://hn.algolia.com/api/v1/search?query="Ask HN: Who is hiring"&tags=story'
)
HN_ITEM_URL = "https://hn.algolia.com/api/v1/items/{item_id}"
HN_COMMENT_URL = "https://news.ycombinator.com/item?id={comment_id}"
_HN_TITLE_RE = re.compile(r"ask hn:\s*who is hiring", re.I)


def find_hn_thread(payload: dict) -> "dict | None":
    """Pick the most recent 'Ask HN: Who is hiring?' story from a search payload."""
    candidates = [
        hit for hit in (payload.get("hits") or [])
        if _HN_TITLE_RE.search(hit.get("title") or "")
    ]
    if not candidates:
        return None
    # whoishiring is the bot that posts the real monthly thread; prefer it,
    # then take the newest by creation date.
    official = [h for h in candidates if (h.get("author") or "").lower() == "whoishiring"]
    pool = official or candidates
    return max(pool, key=lambda h: h.get("created_at_i") or 0)


def normalize_hn(item: dict) -> "list[dict]":
    """Each top-level comment on the thread is one posting."""
    out = []
    for comment in item.get("children") or []:
        if not isinstance(comment, dict):
            continue
        text = html_to_text(comment.get("text"))
        if not text:
            continue  # deleted or empty comment
        header = first_line(text)
        # Company is the text before the first pipe on the header line.
        company = header.split("|")[0].strip() if "|" in header else header.strip()
        company = company.rstrip(" -:,")[:120] or (comment.get("author") or "unknown")
        comment_id = comment.get("id")
        out.append(_posting(
            source="hn",
            company=company,
            title=header,
            location=header,
            remote=None,
            employment_type="",
            url=HN_COMMENT_URL.format(comment_id=comment_id),
            posted_at=comment.get("created_at"),
            description_text=text,
        ))
    return out


def fetch_hn() -> "list[dict]":
    thread = None
    try:
        thread = find_hn_thread(_get_json(HN_SEARCH_BY_DATE_URL))
    except Exception as exc:  # noqa: BLE001 - fall back to the relevance search
        log.warning("hn: date-sorted search failed (%s), falling back", exc)
    if not thread:
        thread = find_hn_thread(_get_json(HN_SEARCH_URL))
    if not thread:
        raise RuntimeError("no 'Ask HN: Who is hiring' thread found in search results")
    item_id = thread.get("objectID") or thread.get("story_id")
    log.info("hn: using thread %s (%s)", item_id, thread.get("title"))
    return normalize_hn(_get_json(HN_ITEM_URL.format(item_id=item_id)))


# --- USAJOBS ----------------------------------------------------------------
#
# Needs a free key from developer.usajobs.gov, sent as Authorization-Key, with
# the registered email address as the User-Agent. Both come from the
# environment (see .env.example); the key is never committed.
#
# NOTE: the response shape below follows USAJOBS' published Search API schema.
# It has NOT been checked against a live response, because the endpoint returns
# 401 to every request without a real key. Every field access is therefore
# defensive. Re-verify the first time a key is available.

USAJOBS_URL = "https://data.usajobs.gov/api/search"
USAJOBS_CATEGORY = "2210"          # IT Specialist
USAJOBS_PAY_GRADE_LOW = "11"
USAJOBS_KEYWORDS = ("solutions", "customer support", "applications", "cybersecurity")

# Location phrasings USAJOBS uses for a role with no fixed duty station.
USAJOBS_REMOTE_LOCATIONS = (
    "anywhere in the u.s.",
    "location negotiable after selection",
)

# RateIntervalCode -> multiplier to reach an annual figure, so a per-hour and a
# per-year posting can be sorted against each other.
_RATE_TO_ANNUAL = {
    "PA": 1.0,        # per annum
    "PH": 2087.0,     # per hour, the OPM work-year
    "PD": 260.0,      # per day
    "PW": 52.0,       # per week
    "BW": 26.0,       # biweekly
    "PM": 12.0,       # per month
}


def usajobs_headers() -> dict:
    """Headers for the USAJOBS API. Raises if the credentials are absent."""
    email = os.environ.get("USAJOBS_EMAIL", "").strip()
    key = os.environ.get("USAJOBS_KEY", "").strip()
    missing = [n for n, v in (("USAJOBS_EMAIL", email), ("USAJOBS_KEY", key)) if not v]
    if missing:
        raise RuntimeError(
            "missing " + ", ".join(missing) + " (get a free key at "
            "https://developer.usajobs.gov and put both in .env)"
        )
    return {
        "Host": "data.usajobs.gov",
        "User-Agent": email,
        "Authorization-Key": key,
    }


def _money(value):
    """USAJOBS sends salaries as strings like "103409.0"."""
    if value in (None, ""):
        return None
    try:
        amount = float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _usajobs_salary(descriptor: dict):
    """Annualized (min, max) across every published remuneration entry."""
    lows, highs = [], []
    for entry in descriptor.get("PositionRemuneration") or []:
        if not isinstance(entry, dict):
            continue
        factor = _RATE_TO_ANNUAL.get((entry.get("RateIntervalCode") or "").upper(), 1.0)
        low = _money(entry.get("MinimumRange"))
        high = _money(entry.get("MaximumRange"))
        if low is not None:
            lows.append(low * factor)
        if high is not None:
            highs.append(high * factor)
    return (min(lows) if lows else None, max(highs) if highs else None)


def _usajobs_remote(descriptor: dict, details: dict, location: str) -> bool:
    """True only on an explicit RemoteIndicator or a no-duty-station location.

    The indicator has appeared in two places across API revisions, so check
    both rather than trusting one.
    """
    for holder in (descriptor, details):
        flag = holder.get("RemoteIndicator")
        if isinstance(flag, bool):
            if flag:
                return True
        elif isinstance(flag, str) and flag.strip().lower() in ("true", "yes", "y"):
            return True
    lowered = (location or "").lower()
    return any(phrase in lowered for phrase in USAJOBS_REMOTE_LOCATIONS)


def _usajobs_who_may_apply(details: dict) -> str:
    who = details.get("WhoMayApply")
    if isinstance(who, dict):
        return ((who.get("Name") or "") + " " + (who.get("Code") or "")).strip()
    return str(who or "").strip()


def normalize_usajobs(payload: dict) -> "list[dict]":
    out = []
    result = payload.get("SearchResult") or {}
    for item in result.get("SearchResultItems") or []:
        if not isinstance(item, dict):
            continue
        descriptor = item.get("MatchedObjectDescriptor") or {}
        details = ((descriptor.get("UserArea") or {}).get("Details")) or {}

        location = descriptor.get("PositionLocationDisplay") or ""
        if not location:
            names = [
                (loc or {}).get("LocationName") or ""
                for loc in descriptor.get("PositionLocation") or []
                if isinstance(loc, dict)
            ]
            location = ", ".join(n for n in names if n)

        schedule = [
            (entry or {}).get("Name") or ""
            for entry in descriptor.get("PositionSchedule") or []
            if isinstance(entry, dict)
        ]
        offering = [
            (entry or {}).get("Name") or ""
            for entry in descriptor.get("PositionOfferingType") or []
            if isinstance(entry, dict)
        ]
        employment_type = ", ".join(v for v in schedule + offering if v)

        # WhoMayApply carries the eligibility restriction the not-open flag
        # looks for, so it has to land in the text the filters read.
        who = _usajobs_who_may_apply(details)
        description = "\n\n".join(part for part in (
            html_to_text(details.get("JobSummary")),
            html_to_text(descriptor.get("QualificationSummary")),
            html_to_text(details.get("Requirements")),
            ("Who may apply: " + who) if who else "",
        ) if part)

        salary_min, salary_max = _usajobs_salary(descriptor)
        agency = (
            descriptor.get("OrganizationName")
            or descriptor.get("DepartmentName")
            or "Federal agency"
        )
        apply_uri = descriptor.get("ApplyURI") or []
        url = descriptor.get("PositionURI") or (
            apply_uri[0] if isinstance(apply_uri, list) and apply_uri else ""
        )

        out.append(_posting(
            source="usajobs",
            company=agency,
            title=descriptor.get("PositionTitle"),
            location=location,
            remote=_usajobs_remote(descriptor, details, location),
            employment_type=employment_type,
            url=url,
            posted_at=descriptor.get("PublicationStartDate")
            or descriptor.get("PositionStartDate"),
            description_text=description,
            salary_min=salary_min,
            salary_max=salary_max,
        ))
    return out


def fetch_usajobs() -> "list[dict]":
    """One search per keyword, de-duplicated on URL.

    A single keyword failing is logged and skipped; only all four failing is
    treated as the source being down.
    """
    headers = usajobs_headers()
    seen, out, failures = set(), [], []
    for keyword in USAJOBS_KEYWORDS:
        query = {
            "JobCategoryCode": USAJOBS_CATEGORY,
            "Keyword": keyword,
            "RemoteIndicator": "True",
            "PayGradeLow": USAJOBS_PAY_GRADE_LOW,
            "ResultsPerPage": "250",
        }
        url = USAJOBS_URL + "?" + urlencode(query)
        try:
            found = normalize_usajobs(_get_json(url, headers=headers))
        except Exception as exc:  # noqa: BLE001 - one keyword must not kill the rest
            log.warning("usajobs keyword %r failed: %s", keyword, exc)
            failures.append(keyword)
            continue
        new = [p for p in found if p["url"] and p["url"] not in seen]
        seen.update(p["url"] for p in new)
        out.extend(new)
        log.info("usajobs %r: %d postings, %d new", keyword, len(found), len(new))
    if failures and len(failures) == len(USAJOBS_KEYWORDS):
        raise RuntimeError("every usajobs keyword search failed")
    return out

# --- Workday ----------------------------------------------------------------
#
# Workday exposes an unauthenticated JSON search per tenant. The slug in
# companies.yaml is the composite "tenant/wdNN/site", e.g. "bah/wd1/BAH_Jobs",
# because all three parts vary per company and are discovered by reading the
# company's public careers redirect.
#
#   POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
#   {"appliedFacets":{},"limit":20,"offset":0,"searchText":"..."}
#
# Verified against the live API: limit is capped at 20 (50 and 100 both return
# zero rows), offset pages correctly, and searchText is a loose stemmed OR
# match rather than a phrase filter - "Claude" returns 977 hits led by "Cloud
# Engineer". The searches only widen the net; the real filtering is the normal
# title rules applied afterwards.

WORKDAY_LIST_URL = "https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
WORKDAY_DETAIL_URL = "https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}"
WORKDAY_PUBLIC_URL = "https://{tenant}.{wd}.myworkdayjobs.com/{site}{path}"

WORKDAY_QUERIES = ("Anthropic", "forward deployed", "Claude", "solutions engineer")
WORKDAY_PAGE_SIZE = 20      # the API's hard cap
WORKDAY_MAX_PAGES = 3       # 60 rows per query; deeper is fuzzy-match noise
WORKDAY_JSON_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

_POSTED_DAYS = re.compile(r"posted\s+(\d+)\+?\s*day", re.I)
_POSTED_TODAY = re.compile(r"posted\s+today", re.I)


def parse_workday_slug(slug: str):
    """"bah/wd1/BAH_Jobs" -> ("bah", "wd1", "BAH_Jobs")."""
    parts = [p for p in (slug or "").split("/") if p]
    if len(parts) != 3:
        raise ValueError(
            "workday slug must be 'tenant/wdNN/site', got " + repr(slug)
        )
    return parts[0], parts[1], parts[2]


def _workday_posted(text: str) -> str:
    """"Posted 7 Days Ago" -> an approximate ISO date.

    Only used when the detail call did not run; the detail payload carries a
    real startDate, which is preferred. "30+ Days Ago" floors at 30, so treat
    anything at that boundary as approximate.
    """
    if not text:
        return ""
    if _POSTED_TODAY.search(text):
        return dt.datetime.now(dt.timezone.utc).date().isoformat()
    match = _POSTED_DAYS.search(text)
    if not match:
        return ""
    days = int(match.group(1))
    return (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days)).isoformat()


def normalize_workday(payload: dict, company: str, tenant: str, wd: str, site: str):
    """List-level rows. Descriptions need a second request per posting, so the
    caller enriches only the ones that survive the title filter."""
    out = []
    for job in payload.get("jobPostings") or []:
        if not isinstance(job, dict):
            continue
        path = job.get("externalPath") or ""
        if not path:
            continue
        out.append(_posting(
            source="workday",
            company=company,
            title=job.get("title"),
            location=job.get("locationsText") or "",
            remote=None,
            employment_type="",
            url=WORKDAY_PUBLIC_URL.format(tenant=tenant, wd=wd, site=site, path=path),
            posted_at=_workday_posted(job.get("postedOn") or ""),
            description_text="",
        ))
    return out


def fetch_workday_detail(path: str, tenant: str, wd: str, site: str) -> dict:
    """Description, real location, schedule and start date for one posting."""
    url = WORKDAY_DETAIL_URL.format(tenant=tenant, wd=wd, site=site, path=path)
    payload = _get_json(url, headers=WORKDAY_JSON_HEADERS)
    info = payload.get("jobPostingInfo") or {}
    return {
        "description_text": html_to_text(info.get("jobDescription")),
        "location": info.get("location") or "",
        "employment_type": info.get("timeType") or "",
        "posted_at": info.get("startDate") or "",
        "url": info.get("externalUrl") or "",
    }


def _workday_search(tenant, wd, site, company, query):
    """All pages for one searchText, up to WORKDAY_MAX_PAGES."""
    url = WORKDAY_LIST_URL.format(tenant=tenant, wd=wd, site=site)
    rows = []
    for page in range(WORKDAY_MAX_PAGES):
        body = {
            "appliedFacets": {},
            "limit": WORKDAY_PAGE_SIZE,
            "offset": page * WORKDAY_PAGE_SIZE,
            "searchText": query,
        }
        response = requests.post(
            url, headers=WORKDAY_JSON_HEADERS, json=body, timeout=TIMEOUT
        )
        response.raise_for_status()
        payload = response.json()
        found = normalize_workday(payload, company, tenant, wd, site)
        rows.extend(found)
        if len(found) < WORKDAY_PAGE_SIZE:
            break
    return rows


def fetch_workday(slug: str, company: str, enrich: bool = True) -> "list[dict]":
    """Four searches, de-duplicated on URL.

    Descriptions cost one request per posting, so only rows whose title already
    matches the include list are enriched. That keeps a daily run to a few dozen
    requests instead of a few thousand, at the cost of never matching a Workday
    posting on description text alone.
    """
    tenant, wd, site = parse_workday_slug(slug)
    seen, rows, failures = set(), [], []

    for query in WORKDAY_QUERIES:
        try:
            found = _workday_search(tenant, wd, site, company, query)
        except Exception as exc:  # noqa: BLE001 - one query must not kill the rest
            log.warning("workday %s query %r failed: %s", company, query, exc)
            failures.append(query)
            continue
        new = [r for r in found if r["url"] not in seen]
        seen.update(r["url"] for r in new)
        rows.extend(new)
        log.info("workday %s %r: %d rows, %d new", company, query, len(found), len(new))

    if failures and len(failures) == len(WORKDAY_QUERIES):
        raise RuntimeError("every workday search failed for " + company)

    if enrich:
        # Imported here rather than at module scope to keep sources.py free of a
        # hard dependency on the filter rules.
        from filters import match_include

        for row in rows:
            if not match_include(row["title"]):
                continue
            path = row["url"].split(site, 1)[-1] if site in row["url"] else ""
            if not path:
                continue
            try:
                detail = fetch_workday_detail(path, tenant, wd, site)
            except Exception as exc:  # noqa: BLE001 - keep the list-level row
                log.warning("workday detail failed for %s: %s", row["url"], exc)
                continue
            for key, value in detail.items():
                if value:
                    row[key] = value
    return rows

# --- orchestration ----------------------------------------------------------

ATS_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
}

BOARD_FETCHERS = {
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "hn": fetch_hn,
    "usajobs": fetch_usajobs,
}


def collect(companies, boards=None, skip_unverified=True):
    """Fetch everything. Returns (postings, errors) where errors is a list of
    (source_label, message). One broken endpoint never stops the run."""
    postings = []
    errors = []

    for entry in companies:
        name = entry.get("name") or entry.get("slug") or "?"
        ats = (entry.get("ats") or "").lower()
        slug = entry.get("slug") or ""
        label = name + " (" + ats + "/" + slug + ")"
        if skip_unverified and entry.get("status") == "unverified":
            log.info("skipping unverified company %s", label)
            continue
        fetcher = ATS_FETCHERS.get(ats)
        if not fetcher:
            errors.append((label, "unknown ats: " + str(ats)))
            continue
        try:
            found = fetcher(slug, name)
            log.info("%s: %d postings", label, len(found))
            postings.extend(found)
        except Exception as exc:  # noqa: BLE001 - one source must not kill the run
            log.warning("%s failed: %s", label, exc)
            errors.append((label, str(exc)))

    for board in (boards if boards is not None else list(BOARD_FETCHERS)):
        fetcher = BOARD_FETCHERS.get(board)
        if not fetcher:
            errors.append((board, "unknown board"))
            continue
        try:
            found = fetcher()
            log.info("%s: %d postings", board, len(found))
            postings.extend(found)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s failed: %s", board, exc)
            errors.append((board, str(exc)))

    return postings, errors
