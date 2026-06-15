# Webnovel

A small set of command-line scripts for discovering, recommending, downloading,
and reading web novels. Everything runs locally and keeps its data on disk.

The project is intended to grow into a site-adapter-based scraper for multiple
web-novel sites. The current implementation supports the novel categories on
[52shuku](https://www.52shuku.net/):

```text
gl  yanqing  bl  xiandaidushi  chongsheng
jiakong  jiakonglishi  chuanyue  wuxia
```

## Scripts

Five standalone scripts live in the repository root. Each has a comprehensive
`--help`:

```text
scrape_metadata.py     Crawl metadata and catalogue graphs for selected categories
download.py  Download full novel text (whole categories, one title/URL, or repair)
recommend.py           Semantic recommendations + embedding-index maintenance
report.py              Catalogue coverage, disk usage, and incomplete-file reports
read.py                Read a downloaded novel: track progress, copy chapters to the clipboard
```

Run any of them with `python <script>.py --help`, and each subcommand also has
its own help, e.g. `python download.py categories --help`.

## Features

- Crawls metadata across selected categories, following previous/next chains and
  recommendation links, and records confirmed 404 pages.
- Produces both recommender metadata and resumable download catalogues.
- Recommends novels using local semantic embeddings, tags, and filters.
- Downloads a single title, a direct URL, selected categories, or everything.
- Reads downloaded novels with a persistent per-novel bookmark and copies the
  next N chapters straight to the clipboard.
- Reports catalogue coverage, incomplete files, and disk usage.
- Supports an optional direct plus Windscribe dual-route bulk downloader.

## Environment

The supported environment is:

```text
~/venvs/recsys/
```

Create it and install the dependencies:

```bash
python3 -m venv ~/venvs/recsys

# Choose the PyTorch build appropriate for the machine. For the existing
# RTX 50-series setup:
~/venvs/recsys/bin/pip install torch \
  --index-url https://download.pytorch.org/whl/cu128

~/venvs/recsys/bin/pip install -r requirements.txt
~/venvs/recsys/bin/pip install -e .
```

`requirements.txt` includes the scraper dependencies as well as NumPy,
sentence-transformers, Transformers, and the optional local-LLM dependencies.

Activate the environment, then run the scripts from the repository root:

```bash
source ~/venvs/recsys/bin/activate
python recommend.py query "破镜重圆，刑侦，ABO"
```

The editable install (`pip install -e .`) makes the `recsys`, `scraper`,
`scripts`, and `webnovel` packages importable. The scripts are run directly with
`python <script>.py` from the repository root.

## Recommended Workflow

### 1. Crawl Metadata

Create or update metadata and catalogues for every category:

```bash
python scrape_metadata.py
```

Select categories:

```bash
python scrape_metadata.py --category yanqing,bl
```

Store a bounded opening excerpt from the first two reading pages:

```bash
python scrape_metadata.py --category all --pages 2
```

Useful options:

```text
--category CATS              Repeatable or comma-separated; default is all
--pages N                    Opening reading pages retained as an excerpt
--limit N                    Maximum new landing pages fetched
--delay SECONDS              Base delay between fetches per route (default 0.1)
--workers N                  Concurrent landing-page requests on the direct route
--refresh                    Re-fetch known records
--recommendation-depth N     Maximum recommendation BFS depth
--windscribe                 Split fetches across direct + Windscribe routes
--windscribe-location LOC    Windscribe location (default: best)
```

The crawler always prioritizes previous/next chain links. Recommendation links
are explored only after the chain frontier is exhausted. The category index page
is checked on every run for new uploads; known catalogue nodes are expanded
without another request, while confirmed 404 records are skipped.

Pass `--windscribe` to roughly double throughput by fetching over two distinct
public IPs (the direct route plus a Windscribe tunnel), one worker per route.
Both routes share a single crawl graph guarded by a lock; only the network
fetches run concurrently. See the [Windscribe](#windscribe) section for setup.

After upgrading from the old GL-only navigation parser, run `--refresh` once for
any non-GL data crawled before that change.

### 2. Build The Index

Embed the crawled metadata so recommendations can run:

```bash
python recommend.py update      # sync downloaded files, then (re)build the index
python recommend.py build       # build the index only
```

`recommend.py sync` extracts metadata from downloaded `<category>/*.txt` files;
`build` reuses vectors whose URL and content hash are unchanged and embeds only
new or changed metadata. `build --rebuild` recomputes everything.

### 3. Get Recommendations

Find books similar to a known title:

```bash
python recommend.py like "Love U2"
```

Describe what you want:

```bash
python recommend.py query "破镜重圆，刑侦，ABO，前任重逢"
```

Browse tags:

```bash
python recommend.py tags 破镜重圆 ABO
```

Keep the model and previous result set loaded:

```bash
python recommend.py repl
```

Common recommendation filters:

```text
-n N                       Number of results
--category gl,yanqing      Restrict categories
--status 完结              Exact status
--min-ch N / --max-ch N    Length range
--year 2024                One year
--year 2020..2025          Inclusive range
--tags TAG1,TAG2           Required/boosted tags
--exclude-author NAME      Exclude authors
--parse                    Let the local LLM parse a free-text query
--rerank                   Local-LLM listwise reranking
--explain                  Local-LLM recommendation explanations
```

### 4. Download Novels

Download a recommendation by title (disambiguated interactively) or by URL:

```bash
python download.py novel "钓系O的端水翻车实录"
python download.py novel https://www.52shuku.net/gl/180.html
```

An unknown but valid URL is downloaded and registered in its category metadata
and catalogue.

Download one or more complete categories:

```bash
python download.py categories gl
python download.py categories yanqing bl --limit 100
python download.py categories all
```

Bulk downloads default to newest first. Use `--forward` for oldest first.
Complete local files and confirmed 404 entries are skipped. `--workers` controls
parallel reading-page requests within the active novel.

Repair files containing failed-page markers:

```bash
python download.py repair --category gl
```

Downloaded novels update their local-file status and bounded metadata record.
They do not automatically rebuild the embedding index:

```bash
python recommend.py update
```

### 5. Read And Annotate

`read.py` is built for reading Chinese in a separate annotation app (pinyin /
dictionary lookup). It remembers where you are in each novel and serves the next
N chapters straight to the clipboard so you can paste them in. On WSL2 the
clipboard is the Windows clipboard (via `clip.exe`).

The bookmark for each novel lives in `data/reading_progress.json` and advances as
you copy, so re-running the command keeps handing you the next chapters.

```bash
# Where am I in this novel?
python read.py "Love U2"

# Copy the next chapter to the clipboard and advance the bookmark
python read.py "Love U2" --copy

# Copy the next 3 chapters and advance
python read.py "Love U2" --copy 3

# Jump to chapter 12, then copy 5 chapters from there
python read.py "Love U2" --chapter 12 --copy 5

# Peek ahead WITHOUT moving the bookmark
python read.py "Love U2" --copy 2 --no-advance

# Print to the terminal instead of the clipboard
python read.py "Love U2" --copy --stdout

# List chapters (▸ marks the next to read), or start over
python read.py "Love U2" --list
python read.py "Love U2" --reset

# Saved progress across all novels
python read.py --progress
```

A novel must be downloaded before it can be read. Clipboard backends are
attempted in this order: `clip.exe`, `wl-copy`, `xclip`, `xsel`, `pbcopy`.

### 6. Reports

```bash
# Per-category catalogue, metadata, and downloaded counts
python report.py catalogue

# Downloaded disk usage
python report.py size

# Incomplete downloaded files (optionally dump the affected URLs)
python report.py incomplete --urls reports/incomplete_urls.txt

# Prev/next chain continuity from the crawl graph
python report.py chains --category gl
```

The `chains` report walks each category's `_catalog.jsonl` and prints its
previous/next chains: unbroken reciprocal segments, confirmed-404 and
non-reciprocal boundaries, and segments merged for display across a shared
break. Categories with no catalogue yet are noted and skipped.

## How The Recommender Works

The recommender operates on metadata, not on entire novel bodies:

```text
landing page or downloaded-file preamble
    ↓
title, author, category, status, date
synopsis, tags, one-line description, intent
optional bounded opening excerpt
    ↓
NovelRecord.embed_text()
    ↓
BAAI/bge-m3 normalized 1024-dimensional vector
    ↓
exact cosine search over the local matrix
    ↓
tag-overlap boost + metadata filters
    ↓
optional local-LLM parsing, reranking, or explanation
```

### Embedding Input

`embed_text()` combines the title, extracted tags, one-line description, cleaned
synopsis, and at most `EXCERPT_MAX_CHARS` characters of the opening excerpt. The
complete downloaded novel body is never embedded.

Metadata crawls normally use only the landing page. Passing `--pages N` lets the
crawler fetch the first few reading pages for a bounded opening excerpt.
Downloaded-file synchronization (`recommend.py sync`) can recover the same
bounded excerpt from the local file.

### Ranking

`like <title>` uses that title's existing vector as the query. `query <text>`
embeds the description and compares it against every stored vector with an exact
NumPy matrix-vector product. The final score is semantic cosine similarity plus a
tag-overlap boost; filters are applied before results are returned.

### Optional Local LLM

The local LLM is loaded only when requested:

- `--parse` extracts semantic text, tags, and filters from a natural-language
  query.
- `--rerank` reads the top candidates and reorders them.
- `--explain` adds a short recommendation reason.

Normal `like`, `query`, and `tags` operations do not load the LLM.

### Incremental Index

Each metadata record stores a SHA-1 hash of `embed_text()`. The index manifest
maps URL to that hash.

```bash
python recommend.py sync     # downloaded files -> metadata store
python recommend.py build    # metadata store  -> embedding index
python recommend.py update   # sync, then build
```

## Storage Layout

Every category is self-contained:

```text
gl/
├── metadata.jsonl
├── _catalog.jsonl
└── YYYY-MM/
    └── title_author_status.txt
```

`metadata.jsonl` contains one recommender record per URL (metadata, synopsis,
tags, description, optional excerpt, `source: "meta"|"full"`, local file path,
and embedding content hash). `_catalog.jsonl` contains the resumable
crawl/download graph (URL, category, `fetch_status`, previous/next URLs,
recommendation URLs, and whether metadata was recorded).

`source: "full"` indicates a verified local file. It does not mean the full body
is included in the embedding input.

The embedding index lives in `data/rec_index/` and is regenerable with
`python recommend.py update`. Reading bookmarks live in
`data/reading_progress.json`. Both are git-ignored.

## Windscribe

Both bulk downloads and metadata scraping can split work across two routes — the
direct route plus a Windscribe tunnel — to fetch over two distinct public IPs:

```bash
python download.py categories all \
  --windscribe --windscribe-location "Singapore - SMRT"

python scrape_metadata.py \
  --windscribe --windscribe-location "Singapore - SMRT"
```

- **Downloads** share one queue: the direct route works newest→oldest, the
  tunnel oldest→newest, so the two ends are consumed without duplicate claims.
- **Scraping** builds one crawl graph from two worker threads (one per route).
  All frontier/graph/counter mutation happens under a lock; only the network
  fetches run concurrently, so no node is fetched twice. Each route honours its
  own `--delay` (default 0.1s), so two routes roughly double throughput.
- Public IPs are compared before the run (skip with `--skip-route-check`).

The implementation binds sessions to the LAN and tunnel interfaces. It disables
the Windscribe firewall because that firewall blocks the interface-bound direct
route. Re-enable it after the run when required:

```bash
windscribe-cli firewall on
```

## Rate Limits And Interruptions

The scraper uses `curl-cffi` browser impersonation, response classification,
retry backoff, and delays between novels. Use conservative worker counts:
repeated missing-page requests are more likely to trigger rate limiting than
requests for valid pages.

Metadata crawling checkpoints its JSONL files periodically and on shutdown
(`Ctrl+C`). Bulk downloads stop claiming new novels after an interrupt and wait
for active novel downloads to finish.

## Development

Run the network-free workflow tests:

```bash
~/venvs/recsys/bin/python -m unittest discover -s tests -v
```

Compile-check all Python modules:

```bash
~/venvs/recsys/bin/python -m py_compile \
  scraper.py recsys/*.py webnovel/*.py scripts/*.py *.py
```

The scraper currently contains 52shuku-specific HTML and URL logic. Supporting
another site should be done by moving those rules behind a site-adapter
interface while retaining the shared download, storage, recommendation, and
reading workflows.

## Responsible Use

This project is intended for personal archival and offline reading. Review a
site's terms and `robots.txt`, keep request rates low, do not bypass paid or
authenticated access, and do not redistribute downloaded works without
permission.
