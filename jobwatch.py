#!/usr/bin/env python3
"""jobwatch - poll job boards daily and email a digest of new matches.

    python jobwatch.py run                 fetch, filter, store, print digest
    python jobwatch.py run --email         ... and send it over SMTP
    python jobwatch.py list --days 7       matches first seen in the last week
    python jobwatch.py add --name X --ats greenhouse --slug x
    python jobwatch.py verify              re-check every slug in companies.yaml
"""
from __future__ import annotations

import argparse
import sys

import config
import digest
import filters
import sources
import store
import verify as verify_mod


def cmd_run(args) -> int:
    log = config.setup_logging(args.verbose)
    config.load_dotenv()

    companies = config.load_companies()
    if not companies:
        print("No companies in companies.yaml - nothing to poll.", file=sys.stderr)
        return 1

    boards = None
    if args.sources:
        wanted = {s.strip().lower() for s in args.sources.split(",")}
        boards = [b for b in sources.BOARD_FETCHERS if b in wanted]
        companies = [c for c in companies if (c.get("ats") or "").lower() in wanted]

    log.info("run start: %d companies, boards=%s", len(companies), boards)
    postings, errors = sources.collect(
        companies, boards=boards, skip_unverified=not args.include_unverified
    )
    log.info("fetched %d raw postings, %d source errors", len(postings), len(errors))

    matches = filters.evaluate_all(postings, match_description=args.match_description)
    log.info("%d postings matched the filters", len(matches))

    n_matched = len(matches)
    if not args.include_closed:
        matches = filters.filter_open(matches)
        log.info("closing-date filter: %d -> %d", n_matched, len(matches))
    n_open = len(matches)
    if args.us_remote:
        matches = filters.filter_us_remote(matches)
        log.info("us-remote filter: %d -> %d", n_open, len(matches))
    n_us_remote = len(matches)
    matches = filters.dedupe(matches)
    log.info("dedupe: %d -> %d", n_us_remote, len(matches))

    conn = store.connect()
    try:
        new_rows = store.upsert_many(conn, matches)
        total = store.counts(conn)["total"]
    finally:
        conn.close()
    log.info("%d new postings (db now holds %d)", len(new_rows), total)

    renderer = digest.render_html if args.html else digest.render
    body = renderer(
        new_rows,
        title="jobwatch: new matches",
        errors=errors,
        empty_note=(
            "No new matches. Scanned " + str(len(postings)) + " postings; "
            + str(n_matched) + " matched the keyword filters, "
            + str(n_open) + " still open, "
            + str(n_us_remote) + " survived the US-remote filter, "
            + str(len(matches)) + " after dedupe"
            + (" - all seen before." if matches else ".")
        ),
        by_tier=args.tier,
    )
    print(body)

    if args.email:
        import mailer

        subject = "jobwatch: " + str(len(new_rows)) + " new match"
        subject += "" if len(new_rows) == 1 else "es"
        try:
            mailer.send_digest(subject, body)
            print("Emailed digest to " + config.env("EMAIL_TO"), file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - a mail failure must not lose the run
            log.error("email failed: %s", exc)
            print("Email failed: " + str(exc), file=sys.stderr)
            return 2

    return 0


def cmd_list(args) -> int:
    config.setup_logging(args.verbose)
    conn = store.connect()
    try:
        rows = store.recent(conn, days=args.days)
    finally:
        conn.close()
    if not args.include_closed:
        rows = filters.filter_open(rows)
    if args.us_remote:
        rows = filters.filter_us_remote(rows)
    rows = filters.dedupe(rows)
    if args.only_tier:
        rows = [r for r in rows if filters.compute_tier(r.get("title") or "") == args.only_tier]
    renderer = digest.render_html if args.html else digest.render
    print(renderer(
        rows,
        title="jobwatch: matches from the last " + str(args.days) + " days",
        empty_note="Nothing recorded in that window.",
        by_tier=args.tier,
    ))
    return 0


def cmd_add(args) -> int:
    config.setup_logging(args.verbose)
    try:
        entry = config.add_company(args.name, args.ats, args.slug)
    except ValueError as exc:
        print("Error: " + str(exc), file=sys.stderr)
        return 1
    print("Added " + entry["name"] + " (" + entry["ats"] + "/" + entry["slug"] + ")")

    ok, detail = verify_mod.probe(entry["ats"], entry["slug"])
    companies = config.load_companies()
    for company in companies:
        if company.get("slug") == entry["slug"] and company.get("ats") == entry["ats"]:
            company["status"] = "verified" if ok else "unverified"
            company["note"] = "" if ok else detail
    config.save_companies(companies)
    print(("  verified: " if ok else "  UNVERIFIED: ") + detail)
    return 0


def cmd_verify(args) -> int:
    config.setup_logging(args.verbose)
    companies = config.load_companies()
    updated = []
    failures = []
    changed = []

    for entry in companies:
        result = verify_mod.verify_company(entry)
        clean = {
            "name": result.get("name"),
            "ats": result.get("ats"),
            "slug": result.get("slug"),
            "status": result.get("status"),
        }
        if result.get("note"):
            clean["note"] = result["note"]
        updated.append(clean)

        marker = "ok  " if result["status"] == "verified" else "FAIL"
        line = (
            marker + "  " + str(result.get("name")) + " -> "
            + str(result.get("ats")) + "/" + str(result.get("slug"))
            + "  (" + str(result.get("jobs", 0)) + " jobs)"
        )
        print(line)
        if result["status"] != "verified":
            failures.append(result)
        elif result.get("slug") != entry.get("slug"):
            changed.append((entry.get("name"), entry.get("slug"), result.get("slug")))

    config.save_companies(updated)

    print("")
    print(str(len(updated) - len(failures)) + "/" + str(len(updated)) + " verified.")
    if changed:
        print("Slugs corrected:")
        for name, old, new in changed:
            print("  " + str(name) + ": " + str(old) + " -> " + str(new))
    if failures:
        print("Marked unverified (skipped on runs):")
        for result in failures:
            print("  " + str(result.get("name")) + " (" + str(result.get("ats")) + ")")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobwatch", description="Poll job boards and digest new matches."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log to stderr too")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="fetch, filter, store and print the digest")
    run.add_argument("--email", action="store_true", help="also send the digest via SMTP")
    run.add_argument(
        "--sources",
        help="comma-separated subset, e.g. greenhouse,ashby,hn (default: all)",
    )
    run.add_argument(
        "--match-description",
        action="store_true",
        help="also match include keywords in the description, not just the title",
    )
    run.add_argument(
        "--tier",
        action="store_true",
        help="group the digest by tier (1 core, 2 adjacent, 3 rest) instead of by company",
    )
    run.add_argument(
        "--us-remote",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="keep only US-remote postings (default: on; --no-us-remote disables)",
    )
    run.add_argument(
        "--html",
        action="store_true",
        help="emit the digest as HTML (used when sending via the Gmail connector)",
    )
    run.add_argument(
        "--include-closed",
        action="store_true",
        help="keep postings whose application deadline has passed (default: drop)",
    )
    run.add_argument(
        "--include-unverified",
        action="store_true",
        help="poll companies marked unverified in companies.yaml",
    )
    run.set_defaults(func=cmd_run)

    listing = sub.add_parser("list", help="show matches already in the database")
    listing.add_argument("--days", type=int, default=7, help="lookback window (default 7)")
    listing.add_argument(
        "--tier", action="store_true", help="group by tier instead of by company"
    )
    listing.add_argument(
        "--only-tier", type=int, choices=(1, 2, 3), help="show only this tier"
    )
    listing.add_argument(
        "--html", action="store_true", help="emit the digest as HTML"
    )
    listing.add_argument(
        "--include-closed",
        action="store_true",
        help="keep postings whose application deadline has passed (default: drop)",
    )
    listing.add_argument(
        "--us-remote",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="keep only US-remote postings (default: on; --no-us-remote disables)",
    )
    listing.set_defaults(func=cmd_list)

    add = sub.add_parser("add", help="append a company to companies.yaml")
    add.add_argument("--name", required=True)
    add.add_argument("--ats", required=True, choices=config.VALID_ATS)
    add.add_argument("--slug", required=True)
    add.set_defaults(func=cmd_add)

    check = sub.add_parser("verify", help="re-check every slug in companies.yaml")
    check.set_defaults(func=cmd_verify)

    return parser


def main(argv=None) -> int:
    # Job descriptions routinely contain em dashes, curly quotes and CJK text.
    # On Windows stdout defaults to cp1252, which raises UnicodeEncodeError on
    # anything outside it - that would kill an otherwise complete run at the
    # moment it prints the digest. Force UTF-8 and never fail on a character.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
