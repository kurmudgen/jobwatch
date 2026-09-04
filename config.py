"""Loading and editing companies.yaml, plus logging setup."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
COMPANIES_FILE = ROOT / "companies.yaml"
DB_FILE = ROOT / "jobwatch.db"
LOG_FILE = ROOT / "jobwatch.log"

VALID_ATS = ("greenhouse", "lever", "ashby", "workday")


def setup_logging(verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("jobwatch")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(console)
    return logger


def load_companies(path: "Path | None" = None) -> "list[dict]":
    path = path or COMPANIES_FILE
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if isinstance(data, list):  # tolerate a bare list at the top level
        return data
    return data.get("companies") or []


def save_companies(companies: "list[dict]", path: "Path | None" = None) -> None:
    path = path or COMPANIES_FILE
    payload = {"companies": companies}
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )


def add_company(name: str, ats: str, slug: str, path: "Path | None" = None) -> dict:
    """Append a company, refusing exact duplicates. Returns the new entry."""
    ats = ats.lower().strip()
    if ats not in VALID_ATS:
        raise ValueError("ats must be one of: " + ", ".join(VALID_ATS))
    if ats == "workday" and len([x for x in slug.split("/") if x]) != 3:
        raise ValueError(
            "a workday slug is 'tenant/wdNN/site', e.g. bah/wd1/BAH_Jobs"
        )
    companies = load_companies(path)
    for existing in companies:
        if (existing.get("ats") or "").lower() == ats and existing.get("slug") == slug:
            raise ValueError(
                "already present: " + str(existing.get("name")) + " (" + ats + "/" + slug + ")"
            )
    entry = {"name": name.strip(), "ats": ats, "slug": slug.strip(), "status": "unchecked"}
    companies.append(entry)
    save_companies(companies, path)
    return entry


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def load_dotenv(path: "Path | None" = None) -> None:
    """Minimal .env reader so `python jobwatch.py run --email` works without
    an extra dependency. Never overwrites an already-set environment variable."""
    path = path or (ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
