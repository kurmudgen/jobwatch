# jobwatch

Polls job boards once a day, filters for customer-facing engineering roles
(support, solutions, implementation, forward-deployed, TAM), and prints a
markdown digest of the postings it has never seen before, sorted into tiers so
it reads in the order worth applying in.

Python 3.11. Dependencies: `requests`, `PyYAML`, `pytest`.

## Install

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
cp .env.example .env                            # then fill in the SMTP values
```

## Commands

```bash
python jobwatch.py run                  # fetch, filter, store, print the digest
python jobwatch.py run --email          # ... and send it over SMTP
python jobwatch.py list --days 7        # matches first seen in the last week
python jobwatch.py list --days 7 --tier         # ... grouped by tier
python jobwatch.py list --days 7 --only-tier 1  # ... tier 1 only
python jobwatch.py add --name Foo --ats greenhouse --slug foo
python jobwatch.py verify               # re-check every slug in companies.yaml
```

Useful flags on `run`:

| Flag | Effect |
| --- | --- |
| `--sources greenhouse,ashby,hn` | poll only a subset |
| `--match-description` | also match include keywords in the description, not just the title |
| `--tier` | group the digest by tier instead of by company, so it reads in apply order |
| `--no-us-remote` | disable the US-remote filter, which is **on by default** |
| `--include-unverified` | poll companies marked `unverified` (skipped by default) |
| `-v` | mirror the log to stderr as well as `jobwatch.log` |

## Sources

| Source | Endpoint |
| --- | --- |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` |
| Lever | `api.lever.co/v0/postings/{slug}?mode=json` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{slug}` |
| Remotive | `remotive.com/api/remote-jobs?category=software-dev` |
| RemoteOK | `remoteok.com/api` (browser User-Agent required) |
| HN Who Is Hiring | Algolia search for the current thread, then its comments |
| USAJOBS | `data.usajobs.gov/api/search` (needs a free key, see below) |
| Workday | `POST {tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` |

Every posting is normalized to `source, company, title, location, remote,
employment_type, url, posted_at, description_text, salary_min, salary_max`.
Only USAJOBS publishes a salary range; the two salary fields are `None`
everywhere else. Each request has a 20s
timeout and one retry, and each source is wrapped in its own try/except, so a
dead endpoint costs you that source and nothing else. Failures are listed at the
bottom of the digest and written to `jobwatch.log`.

**HN thread selection:** the relevance-ranked Algolia `/search` endpoint returns
20 arbitrary hits, the newest of which is currently a thread from 2020. jobwatch
asks the date-sorted `/search_by_date` endpoint first (filtered to the
`whoishiring` account) and keeps `/search` only as a fallback. Each top-level
comment is one posting; the company is the text before the first pipe.

## Workday (partner / consulting)

Workday exposes an unauthenticated JSON search per tenant. In `companies.yaml`
the slug is the composite **`tenant/wdNN/site`** — all three parts vary per
company and are found by reading the company's public careers redirect:

```yaml
  - {name: Booz Allen Hamilton, ats: workday, slug: bah/wd1/BAH_Jobs}
  - {name: Accenture, ats: workday, slug: accenture/wd103/AccentureCareers}
```

Four `searchText` queries run per company — Anthropic, forward deployed, Claude,
solutions engineer — de-duplicated on URL.

Verified against the live API:

- `limit` is capped at **20**; 50 and 100 both return zero rows. `offset` pages
  correctly, so the fetcher pages 3 deep per query (60 rows).
- `searchText` is a **loose stemmed OR match, not a phrase filter**. "Claude"
  returns 977 hits led by *Cloud Engineer*; "solutions engineer" returns 1053,
  essentially everything containing either word. "Anthropic" is the one precise
  query, returning 2. The searches only widen the net — the normal title rules
  do the real filtering afterwards.
- The list response carries no description. Descriptions cost one extra request
  each, so only rows whose **title** already matches the include list are
  enriched. A daily run is a few dozen requests rather than a few thousand, at
  the cost of never matching a Workday posting on description text alone.
- `postedOn` is relative text ("Posted 7 Days Ago"). The detail call returns a
  real `startDate` and is preferred; the relative string is parsed only as a
  fallback and is approximate ("30+ Days Ago" floors at 30 days).

Results appear under **Partner / consulting** in the digest.

**Expect this section to be empty most days.** Both tenants return plenty of
matching titles — 18 passed the keyword filters on the first live run — but
**zero** survived `--us-remote`, because consulting roles are tied to duty
stations: McLean, Arlington, Fort Sam Houston, Hyderabad, Manila, London. Run
`--no-us-remote` to see them (16 on that run, 10 carrying clearance flags).

### Slalom and Deloitte: skipped, and why

Neither is on Workday. Both are **Avature**, and neither exposes a searchable
listing that plain `requests` can use, so both are recorded `unverified` in
`companies.yaml` with the reason and are skipped on every run. No browser
dependency was added, per the brief.

What was tested:

| Check | Result |
| --- | --- |
| `slalom.wd1/wd3/wd5` Workday tenants | 401 / 422 / 422 |
| `jobs.slalom.com/wday/cxs/...` | 404 HTML — not a Workday custom domain |
| Avature `SearchJobs/feed/` | 200, but a fixed 20 rows |
| `keywords`, `keyword`, `q`, `searchText`, `jobKeyword`, `searchKeyword`, `3_66_3`, `freeText` | all ignored — identical results every time |
| `jobRecordsPerPage=100/200`, `jobOffset=20` | ignored — same 20 rows |
| Deloitte `SearchJobs` HTML with `?keywords=` | 200, but the same 10 jobs |
| Slalom `SearchJobs` HTML | 0 job links — the listing is JS-rendered |

So the only reachable Deloitte/Slalom data is a fixed 20-item recent-jobs
window with no search, which at their posting volume would essentially never
surface a match. Re-check if Avature ever exposes a real search parameter.

Accenture Federal Services has no separate public board — `afs.accenture.com`
does not resolve — so AFS roles come through the main Accenture tenant.

## USAJOBS (federal)

Needs a free key from developer.usajobs.gov. Put both values in `.env` (see
`.env.example`); the key is **never** committed, and `.env` is gitignored:

```
USAJOBS_EMAIL=the address you registered the key with
USAJOBS_KEY=your key
```

`USAJOBS_EMAIL` is sent as the `User-Agent` header, which the API requires, and
the key as `Authorization-Key`. Without them the source raises a clear error,
which `collect()` catches like any other source failure — the run continues and
the digest lists it under "Sources that failed this run".

Four searches run per poll, one per keyword, de-duplicated on URL:

```
JobCategoryCode=2210        IT Specialist
Keyword=                    solutions | customer support | applications | cybersecurity
RemoteIndicator=True
PayGradeLow=11
```

`remote` is set True **only** on an explicit `RemoteIndicator`, or when the
location says "Anywhere in the U.S." or "Location Negotiable After Selection".
The indicator has appeared in two places across API revisions, so both are
checked, and the string `"false"` is not treated as truthy.

Salaries are annualized before comparison — a per-hour posting is multiplied by
the 2087-hour OPM work year — so hourly and yearly roles sort against each other
correctly.

**The include list does not apply to USAJOBS, and it can't.** Federal 2210
titles read `IT Specialist (CUSTSPT)`, `IT Specialist (APPSW)`,
`IT Cybersecurity Specialist (INFOSEC)`. None of them contain "engineer", so the
private-sector include keywords match almost none of them — the source would
fetch hundreds of postings and silently discard nearly all of them. For USAJOBS
the API query *is* the include gate: category 2210, one of four keywords, remote,
GS-11 and above is already tighter than a title regex. Exclusions, the on-site
rule and every flag still apply, and the digest records which keyword matched
with `matched_in: usajobs query`.

`supervisory` was added to the exclusions as the federal spelling of a
management role, alongside manager, director, head of and vp.

**Caveat:** the normalizer follows USAJOBS' published Search API schema but has
**not** been checked against a live response — the endpoint returns 401 to every
request without a real key. Every field access is defensive, and
`tests/fixtures/usajobs.json` is hand-built from the documented schema rather
than captured. Re-capture that fixture and re-check the field names the first
time a key is available.

## Filters

**Include** if the title matches any of: support engineer, customer support
engineer, technical support engineer, solutions engineer, sales engineer,
implementation engineer, forward deployed, deployed engineer, customer engineer,
technical account manager, developer support, developer advocate, integration
engineer, onboarding engineer.

"ai engineer" and "applied ai engineer" are deliberately **not** on that list.
At these companies those titles are senior ML research and modelling roles, not
customer-facing engineering; including them added 22 false positives out of 319.

**Exclude** if the title or employment type contains contract, contractor,
intern, internship, part-time, staff, principal, director, manager, vp or head
of — except that "technical account manager" survives the `manager` rule.

**Exclude** if the location or description mentions hybrid, on-site, onsite, in
office or in-office *without* also mentioning remote. The "unless it also
mentions remote" escape hatch is applied **per field**, which matters more than
it sounds: Palantir ships the boilerplate *"there are a few roles that allow for
'Remote' work on an exceptional basis"* in every description, and that single
word was rescuing 62 roles whose location literally reads `(onsite)`. An on-site
term in the location is therefore decisive unless the location itself says
remote, or the description makes a role-level claim ("remote-first", "fully
remote", "work from anywhere"). That keeps Vercel's `Hybrid - London, Berlin`
role whose description says "this role is remote-first", and drops its
`Partner Solutions Engineer` whose description says "in-person (hybrid)... 3
days per week in office".

**Flag** (keep, but mark for a manual look):

- `eligibility:` the description says "not eligible", "excluding" or "except"
- `states:` two or more US states are named, which usually means a
  hire-in-these-states-only list — check by hand whether Montana is on it
- `not-open:` the eligibility text says "current federal employees only" or
  "internal to agency", so the announcement is closed to the public. USAJOBS
  puts this in `WhoMayApply`, which the normalizer folds into the description
- `clearance:` the description mentions clearance, TS/SCI, top secret, DoD, or
  "secret" within a few words of "clearance". Bare "secret" is not enough — it
  was flagging every Supabase support role because the company blurb says "our
  globally distributed team is our secret weapon"

Matching runs on normalized text (lowercased, whitespace and unicode dashes
collapsed) using word-boundary regexes, so "Support Engineer, **Internal**
Tools" is not dropped as an internship and "**VPC** Networking" is not dropped
as a VP role. The keyword that caused the match is stored and shown in the
digest.

One interpretation worth knowing: the include rule is applied to the **title**
by default, because a keyword like "ai engineer" appears in a large share of
descriptions at these companies and would swamp the digest. `--match-description`
turns on description matching, and the digest says which field matched.

## US-remote filter

**On by default** for both `run` and `list`; `--no-us-remote` turns it off.

A posting is kept only if its **location** contains "remote" *and* either names
the US (United States, USA, US, North America, NA, AMER, Americas, or a state
name or code) or names no country at all ("Remote", "Remote, Global"). Anything
naming a foreign country, city or region is dropped.

Only the location is consulted. A description mentioning the US proves nothing
about where the role is based.

Two ordering details carry the weight:

- **Strong US tokens are checked first**, so a multi-region req like
  `Remote - NA, APAC, EMEA` survives on the strength of NA.
- **State codes are checked last**, after the foreign-place list, so
  `CA-Ontario-Toronto` reads as Canada rather than California. State codes are
  also matched **case-sensitively against the raw location**, so the English
  words "in", "or", "ok", "me" and "hi" cannot be read as Indiana, Oregon,
  Oklahoma, Maine or Hawaii.

On the current data this takes 224 matches down to 35.

## Dedupe

Reqs that share a company and title and differ only by location are collapsed to
one, keeping the US/AMER copy. ElevenLabs posts the same Enterprise Solutions
Engineer req once per country — 18 rows for one job. Applied after the US-remote
filter on `run`, and on read for `list`. 35 rows down to 31.

## Tiers

Every match is assigned a tier, shown in the digest and used to sort it:

USAJOBS matches are not tiered. They go in their own **Federal (USAJOBS)**
section, sorted by salary max descending, with unpublished salaries last.

| Tier | Rule |
| --- | --- |
| 1 | title contains support, solutions, implementation or technical account manager, **and** no seniority word |
| 2 | forward deployed / deployed engineer (FDE), or customer engineer |
| 3 | everything else, including a tier-1 title carrying a seniority word |

Seniority words: senior, sr, lead, chief, distinguished, advanced, staff,
principal, director, head, vp, iii, iv. `II` is deliberately absent — "Support
Engineer II" is a mid-level role and stays tier 1. Word boundaries apply, so
"Support Engineer, Sri Lanka" is not read as "Sr" and "Leading Platform" is not
read as "Lead".

`--tier` groups the digest by tier so it reads top-down in the order worth
applying in; without it the digest groups by company and sorts each company's
roles by tier. Tier is derived from the title at render time rather than stored,
so rows written before tiering existed sort correctly with no migration.

## Storage

SQLite at `jobwatch.db`, table `postings`, primary key `url`. Columns added
after first release are applied by an additive migration on `connect()` —
`store.LATER_COLUMNS` is ADD-only, never a drop or rewrite, so an older database
keeps every row. Each run inserts
postings it has never seen and bumps `last_seen` / `seen_count` on ones it has.
Only rows that were new to the database appear in the digest. `list --days N`
reads back by `first_seen`.

## companies.yaml

```yaml
companies:
  - name: Supabase
    ats: ashby        # greenhouse | lever | ashby
    slug: supabase
    status: verified  # verified | unverified | unchecked
```

`python jobwatch.py verify` probes every entry. If the seeded slug 404s it tries
name-derived variants (lowercase, hyphenated, no spaces, suffix-stripped) and
records whichever worked; if none work the entry is marked `unverified` and runs
skip it.

A 200 is not treated as proof of identity for a *guessed* slug. The Greenhouse
slug `shield` returns a live board that belongs to a GitHub Pages demo, not
Shield AI, so a variant must additionally match the board name (Greenhouse
`/v1/boards/{slug}`) or have the slug in its posting URLs (Lever, Ashby). Seeded
slugs are taken at face value, because plenty of real boards apply through a
custom domain and their URL host proves nothing either way.

### Seed verification results (2026-09-02): 40 of 50 verified

Slug corrected by variant search:

| Company | Seeded | Working |
| --- | --- | --- |
| Neon | `ashby/neondatabase` | `ashby/neon` |
| Cursor (Anysphere) | `ashby/anysphere` | `ashby/cursor` |

Recovered by probing the other two ATSes — the platform was wrong, not the slug:

| Company | Specified | Actual |
| --- | --- | --- |
| Sentry | greenhouse | `ashby/sentry` |
| Notion | greenhouse | `ashby/notion` |
| Temporal | greenhouse | `ashby/temporal` |
| Snowflake | greenhouse | `ashby/snowflake` |
| Airbyte | greenhouse | `ashby/airbyte` |
| Vannevar Labs | ashby | `greenhouse/vannevarlabs` |

Still `unverified` — no public Greenhouse, Lever or Ashby board found under any
tried variant, so these are skipped on every run: **Expo, Retool, GitHub,
Fly.io, HashiCorp, dbt Labs, Weights & Biases, Shield AI, Second Front,
Govini**. They are self-hosting, on a different ATS (Workday, Rippling, Ripplematch
and similar), or have no public board. Re-check with `python jobwatch.py verify`;
if you find the right board, `python jobwatch.py add --name X --ats Y --slug Z`.

Verified but currently empty: **Rebellion Defense** (real board, zero open roles).

## Daily use

There is no cron job and no scheduled task. Ask Claude Code once a day:

> run jobwatch and send me the digest

It runs `python jobwatch.py run --tier`, renders the HTML digest with
`--html`, and mails it through the Gmail connector attached to the session.
That keeps the SMTP App Password off disk entirely.

**Send the digest as an attachment, not in the body.** Gmail rewrites every URL
in a message body into a `google.com/url?q=...&ust=1788...` tracking redirect,
and the connector's quoted-printable encoder then eats the `=` in `ust=17...`
because `=17` is a valid quoted-printable escape for byte 0x17. The link arrives
containing a control character. This affects the plain-text body *and* HTML
`href` attributes, and it will keep happening for years, since every current
unix-millisecond timestamp starts with `17`.

Attachments are base64-encoded and are not rewritten, so links inside an
attached HTML file survive intact. The working recipe:

```bash
python jobwatch.py run --tier                 # store + print markdown
python jobwatch.py list --days 1 --html       # HTML for the attachment
```

then base64 the HTML and send it as a `text/html` attachment, keeping URLs out
of the message body entirely. Verify after sending: re-read the message and
check that no `google.com/url` wrapper and no control byte appears where a URL
should be.

## Optional: SMTP

`run --email` sends over SMTP using `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASS` and `EMAIL_TO` (see `.env.example`). Port 465 uses implicit TLS,
anything else STARTTLS. `EMAIL_TO` may be comma-separated. Whitespace in
`SMTP_PASS` is stripped, so a Gmail App Password can be pasted exactly as
Google displays it. The subject is forced to ASCII.

This path is **not configured and has never sent a real message.** Its MIME
output has been verified to round-trip byte-identically with all URLs intact,
but no live send has been made. A failed send does not lose the run: postings
are already stored and the digest already printed. The command exits 2.

## Tests

```bash
.venv/bin/python -m pytest
```

Covers the include / exclude / flag rules (including the word-boundary and
`technical account manager` edge cases) and one normalizer per source against
small payloads saved from the live APIs in `tests/fixtures/`.

## Files

```
jobwatch.py      CLI entry point
sources.py       fetchers + normalizers, one per source
filters.py       include / exclude / flag rules
store.py         SQLite persistence
digest.py        markdown rendering
mailer.py        optional SMTP delivery (unconfigured)
verify.py        slug verification and variant search
config.py        companies.yaml, logging, .env reader
textutil.py      HTML-to-text and text normalization
companies.yaml   the boards to poll
```
