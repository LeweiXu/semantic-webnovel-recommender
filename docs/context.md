# Project Context

## Purpose

This repository is a local webnovel discovery, recommendation, download, and
reading system. The long-term design is site-agnostic; the current scraper
implementation supports 52shuku.

The public entry points are five standalone scripts in the repository root:
`scrape_metadata.py`, `download.py`, `recommend.py`, `report.py`, and
`read.py`. The earlier unified `webnovel` CLI and Textual application were
removed in favour of these focused scripts.

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
python scrape_metadata.py                          # update metadata + catalogues
python recommend.py update                         # sync + (re)build the index
python recommend.py like TITLE
python recommend.py query TEXT
python download.py novel TARGET
python download.py categories gl yanqing
python read.py TARGET --copy 3                      # copy next 3 chapters to the clipboard
python report.py catalogue
```

Each script and each subcommand exposes a comprehensive `--help`.

## Architecture

```text
Root scripts (entry points):
    scrape_metadata.py     -> recsys.crawl.crawl
    download.py  -> webnovel.downloads (+ targets, reports)
    recommend.py           -> recsys.cli.main
    report.py              -> webnovel.reports
    read.py                -> webnovel.library + webnovel.progress + targets

webnovel/                  shared workflow library (no CLI/TUI)
    downloads.py    catalogue and single-novel download workflows
    library.py      local chapter reader, clipboard, and live first-chapter preview
    targets.py      title/URL resolution
    reports.py      catalogue/library reports
    progress.py     persistent per-novel reading bookmarks

scraper.py          52shuku page parsing, fetching, full-text extraction, logs

recsys/
    crawl.py        multi-category metadata/catalogue graph crawler (concurrent)
    catalog.py      per-category crawl ledgers
    store.py        per-category recommender metadata
    extract.py      downloaded-file metadata synchronization
    index.py        incremental embedding index
    search.py       exact cosine search, tag boost, and filters
    routes.py       direct + Windscribe-tunnel session helpers (shared)
    cli.py          recommendation command implementations
```

`recsys/routes.py` holds the Windscribe setup and interface-bound session
helpers shared by `webnovel.downloads` and `recsys.crawl`. The earlier
single-purpose scrapers (`scrape_from_catalogue.py`, `scrape_from_walk.py`,
`scripts/create_catalogue.py`) were removed; their operations are reachable
through the root scripts.

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
7. Checkpoints periodically and on shutdown (`Ctrl+C`).

### Concurrency

The crawl runs N worker threads (one per route). Each worker loops: claim the
next fetchable url (under a lock, expanding known nodes inline), fetch it over
its own session (no lock held), then integrate the result (under the lock,
enqueuing neighbours and checkpointing). All frontier/graph/counter state is
mutated only under `MetaCrawler._lock`; only network fetches run in parallel, so
no node is fetched twice. An `_active` in-flight counter plus a `Condition`
ensures the run ends only when the frontier is empty and no fetch is
outstanding. Without `--windscribe`, `--workers N` threads share one direct
session; with `--windscribe`, two threads run the direct route and a tunnel route
(via `recsys.routes.open_windscribe_routes`). Each route waits `--delay` (default
0.1s, lightly jittered) between its fetches.

## Downloads

Catalogue downloads derive pending work from `_catalog.jsonl` and complete
local files.

Default order is newest to oldest. `--forward` selects oldest to newest.
Optional Windscribe mode uses direct and VPN-bound sessions sharing one
collision-free queue. `Ctrl+C` stops claiming new work and waits for active
novel downloads to finish before exit.

## Reading

`read.py` reads downloaded novels only. It keeps a per-novel bookmark in
`data/reading_progress.json` (via `webnovel.progress`) and copies the next N
chapters from the bookmark to the system clipboard, advancing the bookmark so
repeated runs serve successive chapters. On WSL2 the clipboard is the Windows
clipboard (`clip.exe`). This supports reading in an external pinyin/dictionary
annotation app.

## Recommender

The recommender implementation is independent of the download and reading
workflows.

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

`scraper.py` retains the existing operational and run-log behavior: file logs
and run-log footers are written normally during crawls and downloads.

## Verification

Network-free checks:

```bash
~/venvs/recsys/bin/python -m unittest discover -s tests -v
~/venvs/recsys/bin/python -m py_compile \
  scraper.py recsys/*.py webnovel/*.py scripts/*.py *.py
```

The `Love U2` recommendation regression is:

```bash
python recommend.py like "Love U2" -n 3
```
