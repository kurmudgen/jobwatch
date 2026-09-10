"""Markdown digest rendering."""
from __future__ import annotations

import datetime as dt

from filters import TIER_LABELS, compute_tier


def _tier(posting: dict) -> int:
    """Tier is derived from the title, so rows stored before tiering existed
    still sort correctly without a schema migration.

    The lottery flag has to be passed through: tier is not a stored column, so
    a lottery SWE title read back from SQLite would otherwise recompute as tier
    3 and never reach the Lottery section.
    """
    tier = posting.get("tier")
    if tier in (1, 2, 3):
        return tier
    return compute_tier(posting.get("title") or "",
                        lottery=bool(posting.get("lottery")),
                        defense=bool(posting.get("defense")))


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


FEDERAL_SOURCE = "usajobs"
FEDERAL_HEADING = "Federal (USAJOBS)"
PARTNER_SOURCE = "workday"
PARTNER_HEADING = "Partner / consulting"
LOTTERY_HEADING = "Lottery"
LOTTERY_TIERS = (1, 2)


def is_lottery_pick(posting) -> bool:
    """A lottery company's tier 1 or tier 2 match. Tier 3 stays where it is."""
    return bool(posting.get("lottery")) and _tier(posting) in LOTTERY_TIERS


def _split_federal(postings):
    """(federal, partner, lottery, everything else).

    Each posting lands in exactly one section, so the totals do not double
    count. A lottery company's tier 3 match is not a lottery pick and stays in
    the ordinary tier listing.
    """
    federal = [p for p in postings if p.get("source") == FEDERAL_SOURCE]
    partner = [p for p in postings
               if p.get("source") == PARTNER_SOURCE and p not in federal]
    taken = federal + partner
    lottery = [p for p in postings if p not in taken and is_lottery_pick(p)]
    rest = [p for p in postings if p not in taken and p not in lottery]
    return federal, partner, lottery, rest


def _lottery_order(postings):
    """Salary max first where published, then newest.

    Greenhouse publishes no pay at all - zero populated pay fields across
    Databricks, Coinbase, Roblox, Pinterest and GitLab - so in practice this
    is date order, and the salary key only does anything for a source that
    actually reports one.
    """
    return sorted(
        postings,
        key=lambda p: (
            -(p.get("salary_max") or 0),
            p.get("posted_at") or "",
        ),
        reverse=False,
    ) if any(p.get("salary_max") for p in postings) else sorted(
        postings, key=lambda p: (p.get("posted_at") or ""), reverse=True
    )


def _render_lottery_md(lottery, lines):
    lines.append("## " + LOTTERY_HEADING + " (" + str(len(lottery)) + ")")
    lines.append("_tier 1 and 2 at lottery-tagged companies; "
                 "salary first where published, otherwise newest_")
    lines.append("")
    for posting in _lottery_order(lottery):
        lines.append("- **" + (posting.get("company") or "").strip()
                     + " - " + (posting.get("title") or "(untitled)").strip()
                     + "**  [T" + str(_tier(posting)) + "]")
        bits = []
        if posting.get("salary_max"):
            bits.append(_salary_text(posting))
        if posting.get("location"):
            bits.append((posting.get("location") or "").strip()[:110])
        if posting.get("employment_type"):
            bits.append((posting.get("employment_type") or "").strip())
        posted = _short_date(posting.get("posted_at") or "")
        if posted:
            bits.append("posted " + posted)
        bits.append(str(posting.get("source")))
        keyword = posting.get("matched_keyword")
        if keyword:
            bits.append('matched "' + keyword + '" in '
                        + str(posting.get("matched_in") or "title"))
        lines.append("  - " + " | ".join(bits))
        flag_text = _flag_line(posting)
        if flag_text:
            lines.append("  - FLAGS: " + flag_text)
        lines.append("  - " + (posting.get("url") or ""))
    lines.append("")


def _render_partner_md(partner, lines):
    lines.append("## " + PARTNER_HEADING + " (" + str(len(partner)) + ")")
    lines.append("")
    for posting in sorted(partner, key=lambda p: ((p.get("company") or "").lower(),
                                                  (p.get("title") or "").lower())):
        lines.append("- **" + (posting.get("company") or "").strip()
                     + " - " + (posting.get("title") or "(untitled)").strip() + "**")
        bits = []
        if posting.get("location"):
            bits.append((posting.get("location") or "").strip()[:120])
        if posting.get("remote"):
            bits.append("remote")
        if posting.get("employment_type"):
            bits.append((posting.get("employment_type") or "").strip())
        posted = _short_date(posting.get("posted_at") or "")
        if posted:
            bits.append("posted " + posted)
        keyword = posting.get("matched_keyword")
        if keyword:
            bits.append('matched "' + keyword + '"')
        lines.append("  - " + " | ".join(bits))
        flag_text = _flag_line(posting)
        if flag_text:
            lines.append("  - FLAGS: " + flag_text)
        lines.append("  - " + (posting.get("url") or ""))
    lines.append("")


def _by_salary_desc(postings):
    """Highest salary max first. Postings with no published range sort last,
    then alphabetically so the order is stable."""
    return sorted(
        postings,
        key=lambda p: (
            -(p.get("salary_max") or 0),
            (p.get("company") or "").lower(),
            (p.get("title") or "").lower(),
        ),
    )


def _money(value):
    if not value:
        return ""
    return "$" + format(int(round(float(value))), ",")


def _salary_text(posting) -> str:
    low, high = posting.get("salary_min"), posting.get("salary_max")
    if high and low:
        return _money(low) + " - " + _money(high)
    if high:
        return "up to " + _money(high)
    if low:
        return "from " + _money(low)
    return "salary not published"


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
    federal, partner, lottery, postings = _split_federal(postings)

    if not postings and not federal and not partner and not lottery:
        lines.append("_" + empty_note + "_")
    else:
        every = postings + federal + partner + lottery
        total = len(every)
        flagged = sum(1 for p in every if p.get("flags"))
        summary = str(total) + " match" + ("" if total == 1 else "es")
        summary += " across " + str(len({p.get("company") for p in every})) + " companies"
        if flagged:
            summary += " (" + str(flagged) + " flagged for manual review)"
        lines.append(summary)

        tally = {1: 0, 2: 0, 3: 0}
        for posting in postings:
            tally[_tier(posting)] += 1
        tier_line = ("Tier 1: " + str(tally[1]) + " | Tier 2: " + str(tally[2])
                     + " | Tier 3: " + str(tally[3]))
        if federal:
            tier_line += " | Federal: " + str(len(federal))
        if partner:
            tier_line += " | Partner: " + str(len(partner))
        if lottery:
            tier_line += " | Lottery: " + str(len(lottery))
        lines.append(tier_line)
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

    if lottery:
        _render_lottery_md(lottery, lines)

    if partner:
        _render_partner_md(partner, lines)

    if federal:
        lines.append("## " + FEDERAL_HEADING + " (" + str(len(federal)) + ")")
        lines.append("_sorted by salary max, highest first_")
        lines.append("")
        for posting in _by_salary_desc(federal):
            lines.append("- **" + (posting.get("company") or "").strip()
                         + " - " + (posting.get("title") or "(untitled)").strip() + "**")
            bits = [_salary_text(posting)]
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
            closes = _short_date(posting.get("closes_at") or "")
            if closes:
                bits.append("closes " + closes)
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


def render_html(
    postings: "list[dict]",
    title: str = "jobwatch digest",
    errors: "list[tuple[str, str]] | None" = None,
    empty_note: str = "No new matches.",
    by_tier: bool = True,
) -> str:
    """HTML digest, for sending through the Gmail connector.

    The plain-text path is not usable there: Gmail rewrites bare URLs into
    google.com/url redirects, and the connector's quoted-printable encoding
    then eats the "=" characters that rewrite introduces, so "ust=1788..."
    arrives as a control byte. In the HTML part the same URL survives intact,
    and an explicit anchor lets the reader see the role rather than the URL.
    """
    import html as _html

    def esc(text):
        return _html.escape(str(text or ""))

    today = dt.datetime.now().strftime("%Y-%m-%d")
    federal, partner, lottery, postings = _split_federal(postings)
    out = ['<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
           'font-size:14px;line-height:1.5;color:#111">']
    out.append("<h2 style='margin:0 0 4px'>" + esc(title) + " &ndash; " + today + "</h2>")

    if not postings and not federal and not partner and not lottery:
        out.append("<p><em>" + esc(empty_note) + "</em></p></div>")
        return "\n".join(out)

    tally = {1: 0, 2: 0, 3: 0}
    for posting in postings:
        tally[_tier(posting)] += 1
    summary = ("<p style='margin:0 0 16px;color:#555'>"
               + str(len(postings) + len(federal) + len(partner) + len(lottery))
               + " matches across "
               + str(len({p.get("company")
                          for p in postings + federal + partner + lottery}))
               + " companies"
               + " &middot; Tier 1: " + str(tally[1])
               + " &middot; Tier 2: " + str(tally[2])
               + " &middot; Tier 3: " + str(tally[3]))
    if federal:
        summary += " &middot; Federal: " + str(len(federal))
    if partner:
        summary += " &middot; Partner: " + str(len(partner))
    if lottery:
        summary += " &middot; Lottery: " + str(len(lottery))
    out.append(summary + "</p>")

    groups = (
        [(TIER_LABELS[t], [p for p in postings if _tier(p) == t]) for t in (1, 2, 3)]
        if by_tier else
        [(c, items) for c, items in _group_by_company(postings)]
    )

    for heading, items in groups:
        if not items:
            continue
        out.append("<h3 style='margin:20px 0 8px;border-bottom:1px solid #ddd;"
                   "padding-bottom:4px'>" + esc(heading) + " (" + str(len(items)) + ")</h3>")
        items = sorted(items, key=lambda p: ((p.get("company") or "").lower(),
                                             (p.get("title") or "").lower()))
        out.append("<ul style='margin:0;padding-left:18px'>")
        for posting in items:
            url = esc(posting.get("url"))
            label = esc(posting.get("company")) + " &ndash; " + esc(posting.get("title"))
            out.append("<li style='margin-bottom:10px'>")
            out.append("<a href='" + url + "' style='font-weight:600;color:#0b57d0;"
                       "text-decoration:none'>" + label + "</a><br>")
            bits = [esc(posting.get("location"))]
            if posting.get("employment_type"):
                bits.append(esc(posting.get("employment_type")))
            posted = _short_date(posting.get("posted_at") or "")
            if posted:
                bits.append("posted " + posted)
            bits.append(esc(posting.get("source")))
            out.append("<span style='color:#666'>" + " &middot; ".join(b for b in bits if b)
                       + "</span>")
            flags = posting.get("flags") or []
            if flags:
                out.append("<br><span style='color:#b3261e'>FLAGS: "
                           + esc(", ".join(flags)) + "</span>")
            out.append("</li>")
        out.append("</ul>")

    if lottery:
        out.append("<h3 style='margin:20px 0 8px;border-bottom:1px solid #ddd;"
                   "padding-bottom:4px'>" + esc(LOTTERY_HEADING)
                   + " (" + str(len(lottery)) + ")</h3>")
        out.append("<ul style='margin:0;padding-left:18px'>")
        for posting in _lottery_order(lottery):
            out.append("<li style='margin-bottom:10px'>")
            out.append("<a href='" + esc(posting.get("url")) + "' style='font-weight:600;"
                       "color:#0b57d0;text-decoration:none'>"
                       + esc(posting.get("company")) + " &ndash; "
                       + esc(posting.get("title")) + "</a> "
                       + "<span style='color:#888'>T" + str(_tier(posting))
                       + "</span><br>")
            bits = []
            if posting.get("salary_max"):
                bits.append("<strong>" + esc(_salary_text(posting)) + "</strong>")
            if posting.get("location"):
                bits.append(esc(posting.get("location")))
            posted = _short_date(posting.get("posted_at") or "")
            if posted:
                bits.append("posted " + posted)
            out.append("<span style='color:#666'>"
                       + " &middot; ".join(b for b in bits if b) + "</span>")
            flags = posting.get("flags") or []
            if flags:
                out.append("<br><span style='color:#b3261e'>FLAGS: "
                           + esc(", ".join(flags)) + "</span>")
            out.append("</li>")
        out.append("</ul>")

    if partner:
        out.append("<h3 style='margin:20px 0 8px;border-bottom:1px solid #ddd;"
                   "padding-bottom:4px'>" + esc(PARTNER_HEADING)
                   + " (" + str(len(partner)) + ")</h3>")
        out.append("<ul style='margin:0;padding-left:18px'>")
        for posting in sorted(partner, key=lambda p: ((p.get("company") or "").lower(),
                                                      (p.get("title") or "").lower())):
            out.append("<li style='margin-bottom:10px'>")
            out.append("<a href='" + esc(posting.get("url")) + "' style='font-weight:600;"
                       "color:#0b57d0;text-decoration:none'>"
                       + esc(posting.get("company")) + " &ndash; "
                       + esc(posting.get("title")) + "</a><br>")
            bits = [esc(posting.get("location"))]
            if posting.get("employment_type"):
                bits.append(esc(posting.get("employment_type")))
            posted = _short_date(posting.get("posted_at") or "")
            if posted:
                bits.append("posted " + posted)
            out.append("<span style='color:#666'>"
                       + " &middot; ".join(b for b in bits if b) + "</span>")
            flags = posting.get("flags") or []
            if flags:
                out.append("<br><span style='color:#b3261e'>FLAGS: "
                           + esc(", ".join(flags)) + "</span>")
            out.append("</li>")
        out.append("</ul>")

    if federal:
        out.append("<h3 style='margin:20px 0 8px;border-bottom:1px solid #ddd;"
                   "padding-bottom:4px'>" + esc(FEDERAL_HEADING)
                   + " (" + str(len(federal)) + ")</h3>")
        out.append("<p style='margin:0 0 8px;color:#888;font-size:12px'>"
                   "sorted by salary max, highest first</p>")
        out.append("<ul style='margin:0;padding-left:18px'>")
        for posting in _by_salary_desc(federal):
            out.append("<li style='margin-bottom:10px'>")
            out.append("<a href='" + esc(posting.get("url")) + "' style='font-weight:600;"
                       "color:#0b57d0;text-decoration:none'>"
                       + esc(posting.get("company")) + " &ndash; "
                       + esc(posting.get("title")) + "</a><br>")
            bits = ["<strong>" + esc(_salary_text(posting)) + "</strong>"]
            if posting.get("location"):
                bits.append(esc(posting.get("location")))
            if posting.get("employment_type"):
                bits.append(esc(posting.get("employment_type")))
            posted = _short_date(posting.get("posted_at") or "")
            if posted:
                bits.append("posted " + posted)
            closes = _short_date(posting.get("closes_at") or "")
            if closes:
                bits.append("<strong>closes " + closes + "</strong>")
            out.append("<span style='color:#666'>" + " &middot; ".join(bits) + "</span>")
            flags = posting.get("flags") or []
            if flags:
                out.append("<br><span style='color:#b3261e'>FLAGS: "
                           + esc(", ".join(flags)) + "</span>")
            out.append("</li>")
        out.append("</ul>")

    if errors:
        out.append("<h3 style='margin:20px 0 8px'>Sources that failed this run</h3><ul>")
        for label, message in errors:
            out.append("<li>" + esc(label) + ": " + esc(str(message)[:200]) + "</li>")
        out.append("</ul>")

    out.append("</div>")
    return "\n".join(out)
