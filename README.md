# WebNovel Scraper

An archival scraper for downloading web novels into clean, locally readable
text files.

The long-term goal of this project is to support web-novel sites generally
through site-specific adapters. **The current implementation supports only the
GL (Girls' Love) section of [52shuku](https://www.52shuku.net/gl/).** URL
discovery, navigation labels, metadata selectors, and content parsing are still
specific to that site.

## Project Status

The 52shuku implementation can:

- discover novels from previous/next links and recommendation links;
- preserve confirmed 404s so deleted pages are not repeatedly requested;
- download from a catalogue, newest first by default;
- walk the site's previous/next chain without a catalogue;
- resume catalogue downloads by inspecting completed output files;
- fetch a novel's pages concurrently;
- extract title, author, status, upload date, navigation links, and chapter text;
- remove known inline advertising paragraphs;
- retry temporary failures and record pages that remain incomplete;
- report catalogue coverage, chain breaks, output size, and incomplete novels;
- optionally split catalogue work between direct and Windscribe routes.

Output is currently plain UTF-8 text. Markdown, EPUB, and additional site
adapters are planned but not implemented.

## Requirements

- Python 3.10 or newer
- Internet access to the target site
- A Windscribe CLI installation only when using `--windscribe`

Python dependencies are listed in [`requirements.txt`](requirements.txt):

- `beautifulsoup4`
- `curl-cffi`
- `lxml`

## Installation

```bash
git clone https://github.com/LeweiXu/webnovel-scraper.git
cd webnovel-scraper

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

All commands below assume they are run from the repository root.

## Quick Start

Download up to five novels that are not already present in `output/`:

```bash
python3 scrape_from_catalogue.py --limit 5
```

By default, catalogue downloads run from the most recent novel toward the
oldest. To process the catalogue in chronological order instead:

```bash
python3 scrape_from_catalogue.py --forward --limit 5
```

To download a single URL without placing it in a year/month directory:

```bash
python3 scrape_from_walk.py --get https://www.52shuku.net/gl/180.html
```

## Catalogue Workflow

The canonical catalogue is [`data/gl_catalog.json`](data/gl_catalog.json). It
contains live novels, their graph links and metadata, plus confirmed missing
pages marked with:

```json
{
  "url": "https://www.52shuku.net/gl/example.html",
  "fetch_status": "not_found",
  "title": null,
  "author": null
}
```

### Build or Update the Catalogue

Start a new crawl from a known novel:

```bash
python3 scripts/create_catalogue.py \
  https://www.52shuku.net/gl/180.html
```

Resume from the existing catalogue:

```bash
python3 scripts/create_catalogue.py \
  --seed-map data/gl_catalog.json
```

Useful controls include:

```bash
python3 scripts/create_catalogue.py \
  --seed-map data/gl_catalog.json \
  --budget 500 \
  --delay 0.2 \
  --recommendation-depth 3
```

The crawler prioritizes previous/next chain edges. Recommendation links are a
fallback for reaching disconnected chain segments and are explored
breadth-first to the configured depth. When a recommendation reveals a new
novel, its previous/next links are added back to the higher-priority chain
frontier.

Confirmed 404 URLs are written to the catalogue and skipped on later runs.
Same-shard numeric probing is disabled by default because repeated 404 requests
can trigger rate limiting; enable it deliberately with `--bridge-steps`.

The default outputs are:

- `data/gl_catalog.json`: machine-readable catalogue
- `reports/gl_catalog_report.txt`: crawl and coverage report

### Download from the Catalogue

```bash
python3 scrape_from_catalogue.py
```

Common options:

```text
--catalogue PATH       Catalogue JSON path
--output PATH          Download directory
--limit N              Maximum novels claimed this run; 0 means unlimited
--workers N            Parallel page fetches within each active novel
--forward              Process oldest to newest instead of newest to oldest
--verbose              Print each page fetch on its own line
--chapter-logging      Log chapter character counts instead of page counts
```

Catalogue mode ignores entries whose `fetch_status` is `not_found`. Before
making requests, it scans complete `.txt` files under the output directory and
removes their source URLs from the pending queue.

This mode **does not read or write `state.json`**. Completed output files are
its resume state.

## Chain-Walk Workflow

[`scrape_from_walk.py`](scrape_from_walk.py) follows the 52shuku
`上一篇`/`下一篇` links without relying on the catalogue.

Initialize the walk state from a known novel:

```bash
python3 scrape_from_walk.py \
  --seed https://www.52shuku.net/gl/180.html
```

Continue toward older or newer novels:

```bash
python3 scrape_from_walk.py --backward
python3 scrape_from_walk.py --forward
```

Resume inclusively from a particular URL:

```bash
python3 scrape_from_walk.py \
  --backward \
  --resume https://www.52shuku.net/gl/180.html
```

Repair novels containing failed-page placeholders:

```bash
python3 scrape_from_walk.py --repair
```

Chain-walk mode uses the root-level [`state.json`](state.json) to track the
oldest and newest boundaries and the URLs already visited. It also attempts a
small, reverse-link-verified probe when a navigation link points to a deleted
page.

## Output Format

Catalogue and chain downloads are normally stored by upload month:

```text
output/
└── YYYY-MM/
    └── title_author_status.txt
```

Each file starts with a metadata preamble containing the source URL, upload
date, title, author, status, and previous/next novel links. The remaining text
contains the parsed synopsis and chapters.

If a page still fails after retries, the scraper writes a visible placeholder:

```text
[页面获取失败: https://example.invalid/page]
```

A file containing such a marker is not considered complete and can be found or
repaired later.

Run logs are written under `logs/`. Generated catalogue reports are written
under `reports/`.

## Catalogue Analysis

Visualize reciprocal previous/next chains and their boundaries:

```bash
python3 scripts/analyze_catalogue_chains.py
```

This writes:

- `reports/gl_catalog_chains.txt`: human-readable chains in chronological order
- `data/gl_catalog_chains.json`: structured chain data

Adjacent chain segments separated by one confirmed 404 or a trivial
non-reciprocal link are displayed as one annotated chain rather than as
unrelated fragments.

Find downloaded novels with missing pages:

```bash
python3 scripts/check_incomplete.py
```

Optionally write their source URLs to a report:

```bash
python3 scripts/check_incomplete.py \
  --urls reports/incomplete_novel_urls.txt
```

Summarize downloaded file counts and sizes:

```bash
python3 scripts/report_size.py
```

Additional diagnostic scripts under `scripts/` inspect catalogue URLs, audit
HTML line-break parsing, and probe rate-limit behavior.

## Concurrency and Rate Limits

`--workers` controls concurrent page fetches **within one novel**. It does not
start that many independent novel downloads. The default is intentionally one
worker.

The scraper uses browser impersonation through `curl-cffi`, classifies
Cloudflare challenge and rate-limit responses, and retries temporary failures
with exponential backoff. It also sleeps between novels.

Use conservative worker counts and delays. Existing pages may behave
differently from missing-page probes, and bursts of 404 requests can cause the
site to rate-limit the client.

Pressing Ctrl-C in catalogue mode stops new claims and waits for active novel
downloads to finish so files and run logs are not left half-written.

## Optional Windscribe Mode

Catalogue mode has an experimental dual-route option:

```bash
python3 scrape_from_catalogue.py --windscribe
```

In this mode:

- the direct worker processes newest to oldest;
- the Windscribe worker processes oldest to newest;
- a shared queue prevents duplicate claims;
- `--workers` applies separately to each route;
- the script compares both public IPs before downloading and aborts if they are
  identical.

The implementation is designed for Linux/WSL routing. It connects through
`windscribe-cli`, disables the Windscribe kill-switch firewall, and binds the
direct session to the non-tunnel LAN interface while the VPN session uses the
default tunnel route.

The script does not currently restore the Windscribe firewall preference when
it exits. Re-enable it manually after the run when required:

```bash
windscribe-cli firewall on
```

Prerequisites:

```bash
windscribe-cli login
windscribe-cli status
```

Use `--windscribe-location` to request an available account location and
`--direct-interface` when auto-detection selects the wrong LAN interface:

```bash
python3 scrape_from_catalogue.py \
  --windscribe \
  --windscribe-location "Singapore - SMRT" \
  --direct-interface eth0
```

Do not use `--skip-route-check` unless the routing has been verified
independently. Windscribe behavior varies by client version, platform, protocol,
and firewall configuration.

## Repository Layout

```text
.
├── scraper.py                  Shared 52shuku fetching, parsing, output, and logging
├── scrape_from_catalogue.py   Stateless catalogue downloader
├── scrape_from_walk.py        Stateful previous/next chain walker
├── state.json                 Chain-walk state only
├── requirements.txt
├── data/                       JSON catalogues and machine-readable data
├── docs/                       Site reference files and project context
├── reports/                    Generated human-readable reports
├── scripts/                    Catalogue, analysis, audit, and setup utilities
├── output/                     Downloaded novels, ignored by Git
└── logs/                       Runtime and failure logs, ignored by Git
```

[`scraper.py`](scraper.py) is a library rather than a command-line entry point.
It currently holds both reusable scraping machinery and 52shuku-specific
parsing logic.

## Toward Multi-Site Support

The intended architecture is a common scraping engine with site adapters. A
future adapter should own:

- supported URL detection and normalization;
- landing-page metadata extraction;
- chapter-page discovery and ordering;
- chapter text and synopsis parsing;
- previous/next and recommendation discovery;
- response validation and site-specific block detection;
- filename and metadata normalization where needed.

The shared layer should retain HTTP sessions, retries, concurrency, progress
display, logging, completeness checks, output writers, and resume behavior.

Before another site can be considered supported, the current 52shuku-specific
constants, Chinese navigation labels, URL rules, and HTML selectors in
`scraper.py`, `scrape_from_catalogue.py`, and `scripts/create_catalogue.py` need
to move behind that adapter boundary.

## Responsible Use

This project is intended for personal archival and offline reading. Website
content remains subject to its owners' copyright, terms, and access policies.

Before adapting the scraper to another site:

- review its terms of service and `robots.txt`;
- keep request rates low;
- avoid bypassing authentication or paid access;
- do not redistribute downloaded works without permission;
- stop scraping when the site signals that traffic should be reduced.

Site layouts and anti-bot behavior can change without notice. Treat generated
catalogues and downloaded files as data that should be validated, not as a
permanent API contract.
