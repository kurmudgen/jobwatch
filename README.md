# jobwatch

Polls job boards once a day, filters for support / solutions / AI-engineer style
remote roles, and prints (or emails) a markdown digest of the postings it has
never seen before.

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
python jobwatch.py add --name Foo --ats greenhouse --slug foo
python jobwatch.py verify               # re-check every slug in companies.yaml
```

Useful flags on `run`:

| Flag | Effect |
| --- | --- |
| `--sources greenhouse,ashby,hn` | poll only a subset |
| `--match-description` | also match include keywords in the description, not just the title |
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

Every posting is normalized to `source, company, title, location, remote,
employment_type, url, posted_at, description_text`. Each request has a 20s
timeout and one retry, and each source is wrapped in its own try/except, so a
dead endpoint costs you that source and nothing else. Failures are listed at the
bottom of the digest and written to `jobwatch.log`.

**HN thread selection:** the relevance-ranked Algolia `/search` endpoint returns
20 arbitrary hits, the newest of which is currently a thread from 2020. jobwatch
asks the date-sorted `/search_by_date` endpoint first (filtered to the
`whoishiring` account) and keeps `/search` only as a fallback. Each top-level
comment is one posting; the company is the text before the first pipe.

## Filters

**Include** if the title matches any of: support engineer, customer support
engineer, technical support engineer, solutions engineer, sales engineer,
implementation engineer, forward deployed, deployed engineer, customer engineer,
technical account manager, developer support, developer advocate, applied ai
engineer, ai engineer, integration engineer, onboarding engineer.

**Exclude** if the title or employment type contains contract, contractor,
intern, internship, part-time, staff, principal, director, manager, vp or head
of — except that "technical account manager" survives the `manager` rule.

**Exclude** if the location or description mentions hybrid, on-site, onsite, in
office or in-office *without* also mentioning remote.

**Flag** (keep, but mark for a manual look):

- `eligibility:` the description says "not eligible", "excluding" or "except"
- `states:` two or more US states are named, which usually means a
  hire-in-these-states-only list — check by hand whether Montana is on it
- `clearance:` the description mentions clearance, TS/SCI, secret or DoD

Matching runs on normalized text (lowercased, whitespace and unicode dashes
collapsed) using word-boundary regexes, so "Support Engineer, **Internal**
Tools" is not dropped as an internship and "**VPC** Networking" is not dropped
as a VP role. The keyword that caused the match is stored and shown in the
digest.

One interpretation worth knowing: the include rule is applied to the **title**
by default, because a keyword like "ai engineer" appears in a large share of
descriptions at these companies and would swamp the digest. `--match-description`
turns on description matching, and the digest says which field matched.

## Storage

SQLite at `jobwatch.db`, table `postings`, primary key `url`. Each run inserts
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

## Email

`run --email` reads `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` and
`EMAIL_TO` from the environment (or `.env`; see `.env.example`). Port 465 uses
implicit TLS, anything else uses STARTTLS. `EMAIL_TO` may be a comma-separated
list. The subject is forced to ASCII so it cannot arrive mojibaked. For Gmail,
use an App Password, not the account password.

A failed send does not lose the run: the postings are already stored and the
digest has already been printed. The command exits 2 so cron can tell.

## Scheduling

### cron (Linux / macOS) — 7:00 AM daily

```cron
0 7 * * * /path/to/jobwatch/run.sh --email >> /path/to/jobwatch/cron.log 2>&1
```

`chmod +x run.sh` first. The wrapper cd's to its own directory, picks the venv
interpreter, and sources `.env` — cron starts with almost no environment, so
without that the SMTP variables would be missing.

### Windows Task Scheduler — 7:00 AM daily

PowerShell, one line (run as your own user, not SYSTEM, so it can read `.env`):

```powershell
$dir = "S:\Job search"
$action  = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" -Argument "jobwatch.py run --email" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At 7:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun
Register-ScheduledTask -TaskName "jobwatch" -Action $action -Trigger $trigger -Settings $settings -Description "Daily job digest"
```

`-StartWhenAvailable` makes the task run late if the machine was asleep at 7:00
rather than skipping the day.

Or through the GUI: Task Scheduler → Create Task → Triggers: Daily 7:00 AM →
Actions: Start a program → Program `S:\Job search\.venv\Scripts\python.exe`,
Arguments `jobwatch.py run --email`, Start in `S:\Job search`.

Check it with `Get-ScheduledTaskInfo -TaskName jobwatch`, run it on demand with
`Start-ScheduledTask -TaskName jobwatch`, remove it with
`Unregister-ScheduledTask -TaskName jobwatch`.

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
mailer.py        SMTP delivery
verify.py        slug verification and variant search
config.py        companies.yaml, logging, .env
textutil.py      HTML-to-text and text normalization
companies.yaml   the boards to poll
run.sh           cron wrapper
```
