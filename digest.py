"""Markdown digest rendering."""
from __future__ import annotations

import datetime as dt


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


def render(
    postings: "list[dict]",
    title: str = "jobwatch digest",
    errors: "list[tuple[str, str]] | None" = None,
    empty_note: str = "No new matches.",
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
        lines.append("")

        for company, items in _group_by_company(postings):
            lines.append("## " + company)
            for posting in sorted(items, key=lambda p: (p.get("title") or "").lower()):
                title_text = (posting.get("title") or "(untitled)").strip()
                lines.append("- **" + title_text + "**")

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
                lines.append("  - " + " | ".join(bits))

                flag_text = _flag_line(posting)
                if flag_text:
                    lines.append("  - FLAGS: " + flag_text)
                lines.append("  - " + (posting.get("url") or ""))
            lines.append("")

    if errors:
        lines.append("")
        lines.append("## Sources that failed this run")
        for label, message in errors:
            lines.append("- " + label + ": " + str(message)[:200])
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
