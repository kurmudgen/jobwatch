"""Small HTML-to-text and normalization helpers (no third-party HTML parser)."""
from __future__ import annotations

import html
import re

_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_BLOCK_BREAK = re.compile(r"</?(br|p|div|li|tr|h[1-6]|ul|ol|table)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS_RUNS = re.compile(r"[ \t\r\f\v]+")
_NL_RUNS = re.compile(r"\n{3,}")


def html_to_text(raw: str | None) -> str:
    """Turn an HTML (or HTML-escaped) job description into readable plain text."""
    if not raw:
        return ""
    text = raw
    # Greenhouse double-escapes: content arrives as &lt;p&gt;... Unescape first so
    # the tag-stripping below sees real tags.
    if "&lt;" in text or "&amp;" in text:
        text = html.unescape(text)
    text = _SCRIPT_STYLE.sub(" ", text)
    text = _BLOCK_BREAK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    text = _WS_RUNS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _NL_RUNS.sub("\n\n", text)
    return text.strip()


def normalize(text: str | None) -> str:
    """Lowercase and collapse whitespace/punctuation variants for keyword matching."""
    if not text:
        return ""
    text = text.lower()
    # Unicode dashes and non-breaking spaces confuse literal keyword matching.
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2011", "-")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def first_line(text: str, limit: int = 300) -> str:
    for line in text.split("\n"):
        line = line.strip()
        if line:
            return line[:limit]
    return ""
