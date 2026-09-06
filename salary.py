"""Parse a published pay range out of a job description.

Greenhouse exposes no pay field at all, but US pay-transparency law means many
descriptions carry the range in prose. This reads it out.

Every rule here comes from a real posting in the tier 1 lottery set:

  GitLab      "United States Salary Range $86,500 - $146,400 USD"
  Tailscale   "US Pay Range (OTE) $150,000 - $200,000 USD"        -> OTE, not base
  Tailscale   "CAN Pay Range $84,420 - $132,660 CAD"              -> not USD
  Cresta      "Salary Range: $90,000-$105,000K base + Bonus"      -> stray K suffix
  Cresta      "raised more than $270 million from ... investors"  -> not pay
  Tailscale   "$1500 USD annually for professional development"   -> not pay
  Abnormal    "Base salary range: $25.77 - $37.02 USD"            -> hourly
  LaunchDarkly three geographic zones, each with its own range
  Semgrep     "$217,000 - $272,00 uncapped OTE"                   -> typo in source
"""
from __future__ import annotations

import re

# OPM's work year, the same constant the USAJOBS normalizer uses.
HOURS_PER_YEAR = 2087.0

# A published range is credible only inside these bands. They exist to reject
# funding announcements ("raised $270 million") and perks ("$1500 annually").
MIN_ANNUAL, MAX_ANNUAL = 30_000.0, 1_500_000.0
MIN_HOURLY, MAX_HOURLY = 15.0, 500.0
# A range wider than this is almost always two unrelated numbers.
MAX_SPREAD = 5.0

_AMOUNT = r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*([kK])?"
# Two amounts joined by a dash, "to", or an en/em dash.
_RANGE = re.compile(_AMOUNT + r"\s*(?:-|to|–|—)\s*" + _AMOUNT)

# Words that must appear shortly before a range for it to count as pay.
_PAY_CUE = re.compile(
    r"(salary|pay\s*range|compensation|base\s*pay|base\s*salary|ote|"
    r"on[- ]target\s*earnings|hourly|per\s*hour|wage|"
    # A zone list repeats "Zone N:" before each range and the leading pay
    # header falls outside the lookback by the second zone.
    r"zone\s*\d)",
    re.I,
)
_OTE_CUE = re.compile(r"(\bote\b|on[- ]target\s*earnings|uncapped)", re.I)
_HOURLY_CUE = re.compile(r"(hourly|per\s*hour|/\s*hour|\bhr\b)", re.I)
# The zone a fully-remote candidate outside a major metro actually falls into.
_CATCHALL_CUE = re.compile(r"all\s+other\s+(us|u\.s\.|united states)?\s*locations?", re.I)
_CAD_CUE = re.compile(r"\b(cad|can pay range|canadian)\b", re.I)
_USD_CUE = re.compile(r"\b(usd|us pay range|united states)\b", re.I)

# How far back to look for the cue word. LaunchDarkly puts "Target pay ranges
# based on Geographic Zones for Level 4:" ~130 characters ahead of its first
# range, so 120 was too tight.
_LOOKBACK = 220
# OTE is read from the label before the range, plus only a short tail. Semgrep
# writes "Salary Range: $152,000 - $190,000* USD ($217,000 - $272,00 uncapped
# OTE)" - a wide tail made the BASE range read as OTE.
_OTE_TAIL = 25


def _amount(number: str, suffix: "str | None") -> "float | None":
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    # "$150K" means 150,000. "$105,000K" is a typo and must not become 105 million.
    if suffix and value < 1000:
        value *= 1000
    return value


def _plausible(low: float, high: float, hourly: bool) -> bool:
    if low <= 0 or high <= 0 or high < low:
        return False
    lo_bound, hi_bound = (MIN_HOURLY, MAX_HOURLY) if hourly else (MIN_ANNUAL, MAX_ANNUAL)
    if not (lo_bound <= low <= hi_bound and lo_bound <= high <= hi_bound):
        return False
    return high / low <= MAX_SPREAD


def find_ranges(text: str) -> "list[dict]":
    """Every credible pay range in the text, with its context classified."""
    if not text:
        return []
    found = []
    for match in _RANGE.finditer(text):
        low = _amount(match.group(1), match.group(2))
        high = _amount(match.group(3), match.group(4))
        if low is None or high is None:
            continue

        start = max(0, match.start() - _LOOKBACK)
        before = text[start:match.start()]
        after = text[match.end():match.end() + 40]
        window = before + " " + after
        ote_window = before + " " + text[match.end():match.end() + _OTE_TAIL]

        if not _PAY_CUE.search(before):
            continue  # a bare range with no pay word is not pay

        hourly = bool(_HOURLY_CUE.search(window)) or (low < 1000 and high < 1000)
        if not _plausible(low, high, hourly):
            continue

        currency = "USD"
        if _CAD_CUE.search(window) and not _USD_CUE.search(after):
            currency = "CAD"

        found.append({
            "min": low,
            "max": high,
            "currency": currency,
            "hourly": hourly,
            "ote": bool(_OTE_CUE.search(ote_window)),
            "catchall": bool(_CATCHALL_CUE.search(before[-90:])),
            "annual_min": low * HOURS_PER_YEAR if hourly else low,
            "annual_max": high * HOURS_PER_YEAR if hourly else high,
        })
    return found


def parse_salary(text: str) -> "dict | None":
    """Best single answer for a description.

    Prefers USD over CAD and base pay over OTE, because a base figure is what
    compares meaningfully across postings. When a posting publishes several
    geographic zones the span is widened to cover all of them, and `zones`
    records how many were found.
    """
    ranges = find_ranges(text)
    if not ranges:
        return None

    usd = [r for r in ranges if r["currency"] == "USD"]
    pool = usd or ranges

    base = [r for r in pool if not r["ote"]]
    chosen = base or pool
    kind = "base" if base else "ote"

    catchall = next((r for r in chosen if r.get("catchall")), None)
    return {
        "min": min(r["annual_min"] for r in chosen),
        "max": max(r["annual_max"] for r in chosen),
        "currency": chosen[0]["currency"],
        "hourly": any(r["hourly"] for r in chosen),
        "kind": kind,
        "zones": len(chosen),
        "ote_max": max((r["annual_max"] for r in pool if r["ote"]), default=None),
        # The "all other US locations" band, which is the one that applies to a
        # remote candidate outside the listed metros.
        "catchall_min": catchall["annual_min"] if catchall else None,
        "catchall_max": catchall["annual_max"] if catchall else None,
    }


def describe(parsed: "dict | None") -> str:
    """Short human label, e.g. "$86,500 - $146,400 base"."""
    if not parsed:
        return ""
    def money(value):
        return "$" + format(int(round(value)), ",")
    label = money(parsed["min"]) + " - " + money(parsed["max"])
    if parsed["currency"] != "USD":
        label += " " + parsed["currency"]
    label += " " + parsed["kind"]
    if parsed["hourly"]:
        label += " (hourly, annualized)"
    if parsed["zones"] > 1:
        label += " across " + str(parsed["zones"]) + " zones"
        if parsed.get("catchall_max"):
            label += " (all-other-US: " + money(parsed["catchall_min"]) + " - "                      + money(parsed["catchall_max"]) + ")"
    if parsed["kind"] == "base" and parsed.get("ote_max"):
        label += "; OTE to " + money(parsed["ote_max"])
    return label


def enrich(postings, only=None):
    """Fill salary_min/salary_max from the description where it is missing.

    `only` is a predicate deciding which postings to parse; the caller scopes
    it to tier 1 lottery picks so this never runs over the whole corpus. When a
    posting publishes geographic zones the "all other US locations" band is
    preferred, because that is the one a remote candidate outside the listed
    metros actually falls into.
    """
    for posting in postings:
        if posting.get("salary_max"):
            continue
        if only is not None and not only(posting):
            continue
        parsed = parse_salary(posting.get("description_text") or "")
        if not parsed or parsed["currency"] != "USD":
            posting["salary_note"] = describe(parsed) if parsed else ""
            continue
        posting["salary_min"] = parsed.get("catchall_min") or parsed["min"]
        posting["salary_max"] = parsed.get("catchall_max") or parsed["max"]
        posting["salary_note"] = describe(parsed)
    return postings
