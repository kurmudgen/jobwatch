"""Fetch and normalize postings from every supported source.

Every fetcher returns a list of dicts with exactly these keys:

    source, company, title, location, remote, employment_type,
    url, posted_at, description_text

Network failures are the caller's problem to log; the `fetch_*` helpers raise
and `collect()` isolates each source behind its own try/except so one dead
endpoint never kills a run.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time

import requests

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
    thread = find_hn_thread(_get_json(HN_SEARCH_URL))
    if not thread:
        raise RuntimeError("no 'Ask HN: Who is hiring' thread found in search results")
    item_id = thread.get("objectID") or thread.get("story_id")
    log.info("hn: using thread %s (%s)", item_id, thread.get("title"))
    return normalize_hn(_get_json(HN_ITEM_URL.format(item_id=item_id)))


# --- orchestration ----------------------------------------------------------

ATS_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}

BOARD_FETCHERS = {
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "hn": fetch_hn,
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
