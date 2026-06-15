# Project Context

## Purpose

This repository is a local webnovel discovery, recommendation, download, and
reading system. The long-term design is site-agnostic; the current scraper
implementation supports 52shuku.

The public entry point is the installed `webnovel` command. Running it without
arguments opens the persistent Textual application. Scriptable headless
subcommands remain available through the same executable.

## Environment

The project environment is:

```text
~/venvs/recsys/
```

Install with:

```bash
~/venvs/recsys/bin/pip install -r requirements.txt
~/venvs/recsys/bin/pip install -e .
```

## Public Workflow

```bash
webnovel                         # persistent terminal application
webnovel metadata crawl         # update metadata + catalogues
webnovel recommend like TITLE
webnovel recommend query TEXT
webnovel download novel TARGET
webnovel download categories gl yanqing
webnovel library read TARGET
webnovel index update
```

The application has:

- An upper interaction/results/reader pane.
- A lower persistent scraper/downloader log pane.
- At most one active 52shuku fetch workload.
- Cooperative pause, resume, stop, checkpoint, and graceful quit.
- Exclusive interactive previews/downloads that pause a background fetch job.

## Architecture

```text
pyproject.toml
    console script: webnovel -> webnovel_app.cli:main

webnovel_app/
    cli.py          headless command parser and handlers
    tui.py          persistent Textual application
    jobs.py         single-fetch lifecycle and network exclusion
    downloads.py    catalogue and single-novel download workflows
    library.py      local chapter reader and live first-chapter preview
    targets.py      title/URL resolution
    reports.py      catalogue/library reports

scraper.py          52shuku page parsing, fetching, full-text extraction, logs

recsys/
    crawl.py        multi-category metadata/catalogue graph crawler
    catalog.py      per-category crawl ledgers
    store.py        per-category recommender metadata
    extract.py      downloaded-file metadata synchronization
    index.py        incremental embedding index
    search.py       exact cosine search, tag boost, and filters
    cli.py          recommendation command implementations
```

The old public wrappers (`recommend.py`, `scrape_metadata.py`,
`scrape_from_catalogue.py`, `scrape_from_walk.py`, and
`scripts/create_catalogue.py`) were removed. Their supported operations are all
reachable through `webnovel`.

## Storage

Each category owns its generated data:

```text
gl/
├── metadata.jsonl
├── _catalog.jsonl
└── YYYY-MM/*.txt
```

The same layout applies to:

```text
yanqing  bl  xiandaidushi  chongsheng
jiakong  jiakonglishi  chuanyue  wuxia
```

`metadata.jsonl` is the recommender store. `_catalog.jsonl` is the resumable
crawl graph and includes confirmed 404 records. Downloaded `.txt` files are
organized by upload month.

Generated category directories and `data/rec_index/` are git-ignored.

## Metadata Crawl

`recsys.crawl.MetaCrawler` produces both `metadata.jsonl` and `_catalog.jsonl`.
It:

1. Expands known previous/next graph links without re-fetching valid records.
2. Checks each selected category index for new uploads.
3. Exhausts the chain frontier before recommendation candidates.
4. Uses recommendation links as a bounded BFS fallback.
5. Sends a newly discovered recommendation's previous/next links back to the
   higher-priority chain frontier.
6. Records confirmed 404s so they are not repeatedly requested.
7. Checkpoints periodically, before a cooperative pause, and on shutdown.

## Downloads

Catalogue downloads derive pending work from `_catalog.jsonl` and complete
local files.

Default order is newest to oldest. Headless `--forward` selects oldest to
newest. Optional Windscribe mode uses direct and VPN-bound sessions sharing one
collision-free queue.

The terminal application serializes all site access:

- Only one metadata or category job may run.
- A live preview or individual novel download pauses that job at a safe
  request boundary.
- Interactive requests are also serialized with each other.
- `Ctrl+S` or `/stop` stops new work and preserves completed output.
- `Ctrl+Q` waits for checkpointing and any current novel write before exit.

## Recommender

The recommender implementation is intentionally independent of the TUI.

It embeds:

1. Title
2. Tags
3. One-line description
4. Cleaned synopsis
5. A bounded opening excerpt, when available

It never embeds the complete novel body. `like` reuses an existing record
vector; free-text `query` embeds the user's description. Search is exact cosine
similarity over the local NumPy matrix with a tag-overlap boost and metadata
filters. Index updates reuse unchanged vectors based on content hashes.

## Logging

`scraper.py` retains the existing operational and run-log behavior. In the TUI,
the stream logger and transient page progress callback are redirected to the
lower pane. File logs continue to be written normally.

## Verification

Network-free checks:

```bash
~/venvs/recsys/bin/python -m unittest discover -s tests -v
~/venvs/recsys/bin/python -m py_compile \
  scraper.py recsys/*.py webnovel_app/*.py scripts/*.py
```

The `Love U2` recommendation regression is:

```bash
webnovel recommend like "Love U2" -n 3
```
