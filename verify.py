"""Slug verification: confirm each companies.yaml entry actually returns jobs.

If the seeded slug 404s we try the obvious variants derived from the company
name (lowercase, hyphenated, no spaces, suffix-stripped) and record whichever
one worked. Anything with no working variant is marked `unverified` so a run
skips it instead of failing on it every morning.
"""
from __future__ import annotations

import logging
import re

import requests

from sources import ASHBY_URL, BROWSER_UA, GREENHOUSE_URL, LEVER_URL, TIMEOUT

log = logging.getLogger("jobwatch.verify")

URL_TEMPLATES = {
    "greenhouse": GREENHOUSE_URL,
    "lever": LEVER_URL,
    "ashby": ASHBY_URL,
}

# Words that companies routinely drop from their board slug.
_SUFFIXES = ("labs", "inc", "ai", "io", "technologies", "systems", "industries",
             "software", "corp", "the")


def slug_variants(name: str, seeded: str) -> "list[str]":
    """Ordered, de-duplicated slug candidates: the seeded one first."""
    # Strip a parenthetical alias, e.g. "Cursor (Anysphere)" -> both halves.
    pieces = [name]
    paren = re.search(r"\(([^)]+)\)", name)
    if paren:
        pieces.append(paren.group(1))
        pieces.append(re.sub(r"\s*\([^)]*\)", "", name))

    candidates = [seeded]
    for piece in pieces:
        cleaned = re.sub(r"[^a-z0-9\s\-]", " ", piece.lower()).strip()
        words = [w for w in re.split(r"[\s\-]+", cleaned) if w]
        if not words:
            continue
        candidates.append("".join(words))
        candidates.append("-".join(words))
        trimmed = [w for w in words if w not in _SUFFIXES] or words
        candidates.append("".join(trimmed))
        candidates.append("-".join(trimmed))
        candidates.append(words[0])

    seen = set()
    ordered = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


# A 200 is not proof of identity: Greenhouse hosts demo boards on short slugs
# (e.g. "shield" is a GitHub Pages demo board, not Shield AI). Guessed variants
# therefore have to prove they belong to the company we asked for. Seeded slugs
# are taken at face value - many real boards apply through a custom domain, so
# the apply URL host proves nothing either way.
GREENHOUSE_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}"
_URL_KEYS = ("absolute_url", "hostedUrl", "applyUrl", "jobUrl")
_NOISE_TOKENS = {"the", "inc", "llc", "labs", "ai", "io", "technologies", "systems",
                 "industries", "software", "corp", "company", "co"}


def _tokens(text: str) -> "set[str]":
    return {
        t for t in re.split(r"[^a-z0-9]+", (text or "").lower())
        if t and t not in _NOISE_TOKENS
    }


def board_matches_company(ats: str, slug: str, name: str, jobs: list) -> bool:
    """Best-effort proof that this board really belongs to `name`."""
    company_tokens = _tokens(name)
    if not company_tokens:
        return True

    if ats == "greenhouse":
        try:
            response = requests.get(
                GREENHOUSE_BOARD_URL.format(slug=slug), timeout=TIMEOUT,
                headers={"User-Agent": BROWSER_UA},
            )
            board_name = response.json().get("name") if response.ok else None
        except Exception:  # noqa: BLE001
            board_name = None
        if board_name:
            return bool(_tokens(board_name) & company_tokens)

    # Lever and Ashby put the slug in the canonical posting URL path, so a
    # posting that links somewhere else is not this board.
    for job in jobs[:5]:
        if not isinstance(job, dict):
            continue
        for key in _URL_KEYS:
            value = job.get(key)
            if isinstance(value, str) and value:
                return "/" + slug + "/" in value or "/" + slug + "?" in value
    return False


def _board_jobs(ats: str, slug: str) -> list:
    """Re-read a board's job list for the identity check. Cheap and cached-free."""
    try:
        response = requests.get(
            URL_TEMPLATES[ats].format(slug=slug), timeout=TIMEOUT,
            headers={"User-Agent": BROWSER_UA},
        )
        payload = response.json()
    except Exception:  # noqa: BLE001
        return []
    return payload if isinstance(payload, list) else (payload.get("jobs") or [])


def probe(ats: str, slug: str) -> "tuple[bool, str]":
    """Return (ok, detail). ok means the board returned a usable job list."""
    template = URL_TEMPLATES.get(ats)
    if not template:
        return False, "unknown ats: " + str(ats)
    url = template.format(slug=slug)
    try:
        response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": BROWSER_UA})
    except Exception as exc:  # noqa: BLE001
        return False, "request failed: " + str(exc)
    if response.status_code != 200:
        return False, "HTTP " + str(response.status_code)
    try:
        payload = response.json()
    except ValueError:
        return False, "non-JSON response"
    jobs = payload if isinstance(payload, list) else (payload.get("jobs") or [])
    if not isinstance(jobs, list):
        return False, "unexpected payload shape"
    # An empty board is a real board with nothing open right now, not a bad slug.
    return True, str(len(jobs)) + " jobs"


def verify_company(entry: dict) -> dict:
    """Probe an entry, trying variants. Mutates and returns a copy."""
    result = dict(entry)
    name = entry.get("name") or ""
    ats = (entry.get("ats") or "").lower()
    seeded = entry.get("slug") or ""

    tried = []
    for candidate in slug_variants(name, seeded):
        ok, detail = probe(ats, candidate)
        tried.append(candidate + "=" + detail)
        if ok and candidate != seeded:
            # A guessed slug has to prove it is this company's board, and an
            # empty one proves nothing, so keep looking instead of locking it in.
            if detail.startswith("0 "):
                tried[-1] = candidate + "=empty (unconfirmable variant)"
                continue
            if not board_matches_company(ats, candidate, name, _board_jobs(ats, candidate)):
                tried[-1] = candidate + "=wrong company"
                continue
        if ok:
            result["slug"] = candidate
            result["status"] = "verified"
            result["jobs"] = int(detail.split()[0])
            result["note"] = (
                "" if candidate == seeded
                else "seeded slug '" + seeded + "' failed; using '" + candidate + "'"
            )
            result["tried"] = tried
            return result

    result["status"] = "unverified"
    result["jobs"] = 0
    result["note"] = "no working slug; tried " + ", ".join(tried)
    result["tried"] = tried
    return result
