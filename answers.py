"""Match application-form questions to your standard answers.

Boards word the same question a dozen ways - "Are you legally authorized to
work", "authorised to work full-time in the country where this job is based",
"currently eligible to work in your country of residence". answers.yaml maps a
set of phrases to one answer so each of those resolves to the same line.

answers.yaml is gitignored; answers.example.yaml is the committed template.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from textutil import normalize

ROOT = Path(__file__).resolve().parent
ANSWERS_FILE = ROOT / "answers.yaml"
EXAMPLE_FILE = ROOT / "answers.example.yaml"

# Form labels that the profile block answers directly.
PROFILE_LABELS = {
    "first name": "first_name",
    "last name": "last_name",
    "preferred first name": "preferred_first_name",
    "email": "email",
    "phone": "phone",
    "linkedin profile": "linkedin",
    "website": "website",
    "github handle": "github",
}


def load(path: "Path | None" = None) -> dict:
    """Load answers.yaml. Returns an empty structure if it does not exist."""
    path = path or ANSWERS_FILE
    if not path.exists():
        return {"profile": {}, "answers": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "profile": data.get("profile") or {},
        "answers": data.get("answers") or [],
    }


def answer_for(label: str, book: dict) -> "tuple[str | None, str | None]":
    """(answer, source_id) for a question label, or (None, None).

    Entries are tested in file order, so a combined question like "authorized to
    work without requiring sponsorship" must sit above the single-topic ones.
    An entry with an empty answer counts as unanswered, not as a blank answer.
    """
    normalized = normalize(label)
    if not normalized:
        return None, None

    profile = book.get("profile") or {}
    key = PROFILE_LABELS.get(normalized)
    if key:
        value = str(profile.get(key) or "").strip()
        return (value or None), ("profile." + key if value else None)

    for entry in book.get("answers") or []:
        for phrase in entry.get("match") or []:
            if normalize(phrase) and normalize(phrase) in normalized:
                value = str(entry.get("answer") or "").strip()
                return (value or None), (entry.get("id") if value else None)
    return None, None


def annotate(summary: dict, book: dict) -> dict:
    """Attach (question, answer, source) triples to a form summary."""
    resolved, missing = [], []
    for label in list(summary.get("specific") or []) + list(summary.get("essays") or []):
        if any(label == r[0] for r in resolved) or label in missing:
            continue
        value, source = answer_for(label, book)
        if value:
            resolved.append((label, value, source))
        else:
            missing.append(label)
    out = dict(summary)
    out["resolved"] = resolved
    out["missing"] = missing
    return out


def coverage(rows: "list[dict]", book: dict) -> dict:
    """How many distinct questions across all forms have an answer."""
    seen, answered = set(), set()
    for row in rows:
        form = row.get("form")
        if not form:
            continue
        for label in list(form.get("specific") or []) + list(form.get("essays") or []):
            seen.add(label)
            value, _ = answer_for(label, book)
            if value:
                answered.add(label)
    return {"total": len(seen), "answered": len(answered),
            "missing": sorted(seen - answered)}
