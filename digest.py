"""Markdown digest rendering."""
from __future__ import annotations

import datetime as dt

from filters import TIER_LABELS, compute_tier


def _tier(posting: dict) -> int:
    """Tier is derived from the title, so rows stored before tiering existed
    still sort correctly without a schema migration."""
    tier = posting.get("tier")
    if tier in (1, 2, 3):
        return tier
    return compute_tier(posting.get("title") or "")


def _group_by_company(postings: "list[dict]") -> "list[tuple[str, list[dict]]]":
    groups: "dict[str, list[dict]]" = {}
    for posting in postings:
        company = (posting.get("company") or "Unknown").strip() or "Unknown"
        groups.setdefault(company, []).append(posting)
    return sorted(groups.items(), key=lambda kv: kv[0].lower())


def _flag_line(posting: dict) -> str:
    flags = posting.get("flags") or []
    return " ".join("`" + f + "`" for f in flags)


def _short_date(value: str) -> str:
    if not value:
        return ""
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return str(value)[:10]


def _render_posting(posting: dict, show_company: bool, lines: list) -> None:
    title_text = (posting.get("title") or "(untitled)").strip()
    prefix = (posting.get("company") or "").strip() + " - " if show_company else ""
    lines.append("- **" + prefix + title_text + "**")

    bits = []
    location = (posting.get("location") or "").strip()
    if location:
        bits.append(location[:120])
    if posting.get("remote"):
        bits.append("remote")
    employment_type = (posting.get("employment_type") or "").strip()
    if employment_type:
        bits.append(employment_type)
    posted = _short_date(posting.get("posted_at") or "")
    if posted:
        bits.append("posted " + posted)
    bits.append(str(posting.get("source")))
    keyword = posting.get("matched_keyword")
    if keyword:
        matched_in = posting.get("matched_in") or "title"
        bits.append('matched "' + keyword + '" in ' + matched_in)
    if not show_company:
        bits.append("T" + str(_tier(posting)))
    lines.append("  - " + " | ".join(bits))

    flag_text = _flag_line(posting)
    if flag_text:
        lines.append("  - FLAGS: " + flag_text)
    lines.append("  - " + (posting.get("url") or ""))


def render(
    postings: "list[dict]",
    title: str = "jobwatch digest",
    errors: "list[tuple[str, str]] | None" = None,
    empty_note: str = "No new matches.",
    by_tier: bool = False,
) -> str:
    today = dt.datetime.now().strftime("%Y-%m-%d")
    lines = ["# " + title + " - " + today, ""]

    if not postings:
        lines.append("_" + empty_note + "_")
    else:
        flagged = sum(1 for p in postings if p.get("flags"))
        summary = str(len(postings)) + " match" + ("" if len(postings) == 1 else "es")
        summary += " across " + str(len({p.get("company") for p in postings})) + " companies"
        if flagged:
            summary += " (" + str(flagged) + " flagged for manual review)"
        lines.append(summary)

        tally = {1: 0, 2: 0, 3: 0}
        for posting in postings:
            tally[_tier(posting)] += 1
        lines.append(
            "Tier 1: " + str(tally[1]) + " | Tier 2: " + str(tally[2])
            + " | Tier 3: " + str(tally[3])
        )
        lines.append("")

        if by_tier:
            # Apply order: tier, then company, then title.
            for tier in (1, 2, 3):
                items = [p for p in postings if _tier(p) == tier]
                if not items:
                    continue
                lines.append("## " + TIER_LABELS[tier] + " (" + str(len(items)) + ")")
                items.sort(key=lambda p: ((p.get("company") or "").lower(),
                                          (p.get("title") or "").lower()))
                for posting in items:
                    _render_posting(posting, show_company=True, lines=lines)
                lines.append("")
        else:
            for company, items in _group_by_company(postings):
                lines.append("## " + company)
                for posting in sorted(items, key=lambda p: (_tier(p),
                                                            (p.get("title") or "").lower())):
                    _render_posting(posting, show_company=False, lines=lines)
                lines.append("")

    if errors:
        lines.append("")
        lines.append("## Sources that failed this run")
        for label, message in errors:
            lines.append("- " + label + ": " + str(message)[:200])
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
