"""Application-form triage.

Greenhouse publishes the whole application form on its public board API:

    GET /v1/boards/{slug}/jobs/{id}?questions=true

That is enough to answer "how much work is this one" before opening the tab,
and to collect the questions that repeat across boards so they can be answered
once instead of nineteen times.

Nothing here submits anything. Greenhouse's application POST needs the
employer's board key, which an applicant does not have, and mass auto-submission
is against most boards' terms anyway. This is triage and preparation.
"""
from __future__ import annotations

import logging
import re

import requests

from sources import BROWSER_UA, TIMEOUT

log = logging.getLogger("jobwatch.forms")

GREENHOUSE_JOB_URL = (
    "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?questions=true"
)

# Fields every Greenhouse form has. Anything else is work specific to this job.
BOILERPLATE = {
    "first name", "last name", "preferred first name", "email", "phone",
    "resume/cv", "resume", "cover letter", "linkedin profile", "website",
    "full name", "name",
}

_JOB_ID_RES = (
    re.compile(r"gh_jid=(\d+)"),
    re.compile(r"/jobs/(\d+)"),
    re.compile(r"/detail/(\d+)"),
)


def job_id_from_url(url: str) -> "str | None":
    for pattern in _JOB_ID_RES:
        match = pattern.search(url or "")
        if match:
            return match.group(1)
    return None


def slug_for(company: str, companies: "list[dict]") -> "str | None":
    """Greenhouse board slug for a company name, from companies.yaml.

    The URL is not reliable here: Stripe and Datadog serve their boards from
    stripe.com and careers.datadoghq.com, so the slug never appears in it.
    """
    target = (company or "").strip().lower()
    for entry in companies:
        if (entry.get("ats") or "").lower() != "greenhouse":
            continue
        if (entry.get("name") or "").strip().lower() == target:
            return entry.get("slug")
    return None


def fetch_form(slug: str, job_id: str) -> "dict | None":
    """Questions for one Greenhouse job, or None if the board will not say."""
    url = GREENHOUSE_JOB_URL.format(slug=slug, job_id=job_id)
    try:
        response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": BROWSER_UA})
        if response.status_code != 200:
            return None
        return response.json()
    except Exception as exc:  # noqa: BLE001 - a missing form is not a run failure
        log.warning("form fetch failed for %s/%s: %s", slug, job_id, exc)
        return None


def summarize(payload: dict) -> dict:
    """Required-field count and the questions that are not boilerplate."""
    questions = payload.get("questions") or []
    required = [q for q in questions if q.get("required")]
    specific = [
        q for q in required
        if (q.get("label") or "").strip().lower() not in BOILERPLATE
    ]
    needs_essay = [
        q for q in questions
        if any(f.get("type") == "textarea" for f in (q.get("fields") or []))
        and (q.get("label") or "").strip().lower() not in ("resume/cv", "cover letter")
    ]
    return {
        "total": len(questions),
        "required": len(required),
        "specific": [(q.get("label") or "").strip() for q in specific],
        "essays": [(q.get("label") or "").strip() for q in needs_essay],
        "demographic": bool(payload.get("demographic_questions")),
        "deadline": payload.get("application_deadline"),
    }


def effort(summary: "dict | None") -> int:
    """Rough ordering key: boilerplate-only forms first."""
    if not summary:
        return 999
    return summary["required"] + 3 * len(summary["specific"]) + 5 * len(summary["essays"])


def collect_forms(postings: "list[dict]", companies: "list[dict]") -> "list[dict]":
    """Attach a form summary to every Greenhouse posting it can resolve."""
    out = []
    for posting in postings:
        row = dict(posting)
        row["form"] = None
        row["form_note"] = ""
        if posting.get("source") != "greenhouse":
            row["form_note"] = "not greenhouse"
            out.append(row)
            continue
        slug = slug_for(posting.get("company") or "", companies)
        job_id = job_id_from_url(posting.get("url") or "")
        if not slug:
            row["form_note"] = "no board slug in companies.yaml"
        elif not job_id:
            row["form_note"] = "no job id in url"
        else:
            payload = fetch_form(slug, job_id)
            if payload:
                row["form"] = summarize(payload)
            else:
                row["form_note"] = "board did not return the form"
        out.append(row)
    return out


def render(rows: "list[dict]", book: "dict | None" = None) -> str:
    """Plain-text triage table, easiest application first.

    With an answers book loaded, each question is printed with the answer to
    paste; without one, just the question."""
    import answers as answers_mod
    book = book if book is not None else {"profile": {}, "answers": []}
    resolved = [r for r in rows if r.get("form")]
    unresolved = [r for r in rows if not r.get("form")]
    lines = ["# application triage", ""]

    if resolved:
        lines.append(str(len(resolved)) + " forms read, easiest first")
        lines.append("")
        for row in sorted(resolved, key=lambda r: effort(r["form"])):
            form = row["form"]
            head = ("- " + (row.get("company") or "") + " - " + (row.get("title") or "")
                    + "  [" + str(form["required"]) + " required")
            if form["specific"]:
                head += ", " + str(len(form["specific"])) + " job-specific"
            if form["essays"]:
                head += ", " + str(len(form["essays"])) + " written"
            head += "]"
            lines.append(head)
            annotated = answers_mod.annotate(form, book)
            for label, value, _source in annotated["resolved"]:
                lines.append("    Q " + label)
                indented = value.replace(chr(10), chr(10) + "      ")
                lines.append("    A " + indented)
            for label in annotated["missing"]:
                lines.append("    ? " + label + "   [no answer yet]")
            if form["deadline"]:
                lines.append("    deadline: " + str(form["deadline"]))
            lines.append("    " + (row.get("url") or ""))
        lines.append("")

    if unresolved:
        lines.append("## form not readable (" + str(len(unresolved)) + ")")
        for row in unresolved:
            lines.append("- " + (row.get("company") or "") + " - " + (row.get("title") or "")
                         + "  (" + (row.get("form_note") or "") + ")")
            lines.append("    " + (row.get("url") or ""))
        lines.append("")

    cover = answers_mod.coverage(rows, book)
    if cover["total"]:
        lines.append("## answer coverage: " + str(cover["answered"]) + "/"
                     + str(cover["total"]) + " distinct questions")
        if cover["missing"]:
            counts = {}
            for row in resolved:
                for label in row["form"]["specific"] + row["form"]["essays"]:
                    if label in cover["missing"]:
                        counts[label] = counts.get(label, 0) + 1
            lines.append("")
            lines.append("Still to write, most-repeated first "
                         "(add these to answers.yaml):")
            for label, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                lines.append("- (" + str(count) + "x) " + label)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
