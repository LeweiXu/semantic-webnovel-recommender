# Webnovel

A persistent terminal application for discovering, recommending, downloading,
and reading web novels.

The project is intended to grow into a site-adapter-based scraper for multiple
web-novel sites. The current implementation supports the novel categories on
[52shuku](https://www.52shuku.net/):

```text
gl  yanqing  bl  xiandaidushi  chongsheng
jiakong  jiakonglishi  chuanyue  wuxia
```

## Features

- Crawls metadata across selected categories.
- Produces both recommender metadata and resumable download catalogues.
- Discovers novels through previous/next chains and recommendation links.
- Records confirmed 404 pages so they are not repeatedly requested.
- Recommends novels using local semantic embeddings, tags, and filters.
- Downloads a single title, a direct URL, selected categories, or everything.
- Reads downloaded novels chapter by chapter in the terminal.
- Fetches a bounded first-chapter preview for metadata-only novels.
- Keeps recommendations and reading usable while one background fetch job runs.
- Shows scraper/downloader logs in a dedicated live pane.
- Pauses the background fetch job before an interactive network request.
- Gracefully checkpoints and stops active work without leaving the application.
- Copies one or more chapters to the clipboard.
- Reports catalogue coverage, broken chains, incomplete files, and disk usage.
- Supports an optional direct plus Windscribe dual-route bulk downloader.

Downloaded novels and generated metadata remain local.

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

`requirements.txt` includes the scraper and TUI dependencies as well as NumPy,
sentence-transformers, Transformers, and the optional local-LLM dependencies.

Activate the environment, then run the installed command from any directory:

```bash
source ~/venvs/recsys/bin/activate
webnovel
```

## Terminal Application

Running `webnovel` without arguments opens the persistent full-screen
application:

```bash
webnovel
```

The upper pane holds the command transcript, recommendation/library results,
and reader. The lower pane continuously shows operational logs and page
progress. Only one background 52shuku fetch job is allowed at a time.

Enter a plain reading description to run a semantic recommendation query, or
use slash commands:

```text
/like TITLE                  Recommend books similar to a title
/query DESCRIPTION           Semantic recommendation query
/tags TAG[,TAG]              Browse books carrying all tags
/library [TEXT]              Browse metadata and downloaded books
/info [N|TITLE|URL]          Show a result's metadata
/read [N|TITLE|URL]          Read locally or fetch a first-chapter preview
/download [N|TITLE|URL]      Download one novel
/crawl [cats] [limit]        Update metadata and catalogues
/download-all [cats] [limit] Download pending novels
/job  /pause  /resume  /stop
/help  /clear  /quit
```

Recommendation and library results are keyboard-selectable. Press Enter to
show the selected record; `/read 3` or `/download 3` acts on result 3.

Reader keys:

```text
[ / ]        Previous / next chapter
Esc          Return to the interaction pane
```

Global keys:

```text
Ctrl+K       Focus the command input
Ctrl+P       Pause or resume the active fetch job
Ctrl+S       Gracefully stop and checkpoint the active fetch job
Ctrl+Q       Stop/checkpoint if needed, then quit
```

If `/read` needs a live preview or `/download` requests one novel while a
metadata/category job is active, the application pauses that job at the next
safe request boundary, runs the interactive request exclusively, then resumes.

## Headless CLI

The same executable retains scriptable subcommands:

```text
webnovel
├── metadata crawl|sync-files|status|migrate-legacy-gl
├── download novel|categories|repair
├── recommend like|query|tags|repl
├── library list|info|read
├── index status|update|rebuild
├── report catalogue|chains|incomplete|size|urls
├── watch
└── admin windscribe|audit
```

Use `--help` at any level:

```bash
webnovel --help
webnovel metadata crawl --help
webnovel recommend query --help
```

## Recommended Workflow

### 1. Crawl Metadata

Create or update metadata and catalogues for every category:

```bash
webnovel metadata crawl
```

Select categories:

```bash
webnovel metadata crawl \
  --category yanqing,bl
```

Store a bounded opening excerpt from the first two reading pages:

```bash
webnovel metadata crawl \
  --category all \
  --pages 2
```

Useful options:

```text
--category CATEGORY          Repeatable or comma-separated; default is all
--pages N                    Opening reading pages retained as an excerpt
--limit N                    Maximum new landing pages fetched
--delay SECONDS              Jittered delay between fetch batches
--workers N                  Concurrent landing-page requests
--refresh                    Re-fetch known records
--recommendation-depth N     Maximum recommendation BFS depth
```

The crawler always prioritizes previous/next chain links. Recommendation links
are explored only after the chain frontier is exhausted. A newly discovered
recommendation contributes its previous/next links back to the higher-priority
chain frontier.

The category index page is checked on every run for new uploads. Known
catalogue nodes are expanded without another request, while confirmed 404
records are skipped.

After upgrading from the old GL-only navigation parser, run `--refresh` once
for any non-GL test data crawled before this refactor:

```bash
webnovel metadata crawl \
  --category yanqing \
  --refresh
```

### 2. Get Recommendations

Find books similar to a known title:

```bash
webnovel recommend like "Love U2"
```

Describe what you want:

```bash
webnovel recommend query \
  "破镜重圆，刑侦，ABO，前任重逢"
```

Browse tags:

```bash
webnovel recommend tags 破镜重圆 ABO
```

Keep the model and previous result set loaded:

```bash
webnovel recommend repl
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
--parse                     Let the local LLM parse a free-text query
--rerank                    Local-LLM listwise reranking
--explain                   Local-LLM recommendation explanations
```

### 3. Download Novels

Download a recommendation by title:

```bash
webnovel download novel \
  "钓系O的端水翻车实录"
```

Download a direct URL:

```bash
webnovel download novel \
  https://www.52shuku.net/gl/180.html
```

An unknown but valid URL is downloaded and registered in its category metadata
and catalogue.

Download one or more complete categories:

```bash
webnovel download categories gl

webnovel download categories \
  yanqing bl --limit 100

webnovel download categories all
```

Bulk downloads default to newest first. Use `--forward` for oldest first.
Complete local files and confirmed 404 entries are skipped.

`--workers` controls parallel reading-page requests within the active novel. It
does not start that number of unrelated novel downloads.

Downloaded novels update their local-file status and bounded metadata record.
They do not automatically rebuild the embedding index:

```bash
webnovel index status
webnovel index update
```

Repair files containing failed-page markers:

```bash
webnovel download repair \
  --category gl
```

## Reading And Library

Search local metadata:

```bash
webnovel library list "刑侦"
webnovel library list --downloaded
webnovel library info "Love U2"
```

Open the interactive reader:

```bash
webnovel library read "Love U2"
```

Reader commands:

```text
next / n           Next chapter
previous / p       Previous chapter
goto N / g N       Jump to chapter N
copy N / c N       Copy current chapter and the following N-1 chapters
all / a            Print the whole novel
quit / q           Exit
```

Non-interactive examples:

```bash
# Print chapter 12
webnovel library read "Love U2" --chapter 12

# Copy chapters 12 through 16
webnovel library read \
  "Love U2" --chapter 12 --copy 5

# Print the full novel for redirection or piping
webnovel library read "Love U2" --full
```

Clipboard backends are attempted in this order:

```text
clip.exe  wl-copy  xclip  xsel  pbcopy
```

On the current WSL2 environment, `clip.exe` is used.

If a novel has metadata but has not been downloaded, `library read` fetches a
temporary live preview. It requests pages until the second chapter heading so
the complete first logical chapter can be shown. The default safety limit is 10
reading pages:

```bash
webnovel library read \
  "metadata-only title" --page-limit 15
```

For chapterless novels, the preview displays text up to the page limit. A
warning is printed when the safety limit truncates the preview.

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

`embed_text()` combines:

1. Title
2. Extracted tags
3. One-line description
4. Cleaned synopsis
5. At most `EXCERPT_MAX_CHARS` characters of the opening excerpt

The complete downloaded novel body is never embedded.

Metadata crawls normally use only the landing page. Passing `--pages N` lets
the crawler fetch the first few reading pages for a bounded opening excerpt.
Downloaded-file synchronization can recover the same bounded opening excerpt
from the local file.

### Ranking

`like <title>` uses that title's existing vector as the query.

`query <text>` embeds the user's description. Every stored vector is compared
with an exact NumPy matrix-vector product. At the current corpus size, an
approximate-nearest-neighbor database is unnecessary.

The final score is:

```text
semantic cosine similarity + tag-overlap boost
```

Filters are applied before results are returned. Chapter count is used for
downloaded novels; metadata-only records fall back to reading-page count as a
length proxy.

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
webnovel index status
webnovel index update
webnovel index rebuild
```

`index update` reuses vectors whose URL and content hash are unchanged and
embeds only new or changed metadata. `index rebuild` recomputes everything.

## Storage Layout

Every category is self-contained:

```text
gl/
├── metadata.jsonl
├── _catalog.jsonl
└── YYYY-MM/
    └── title_author_status.txt

yanqing/
├── metadata.jsonl
├── _catalog.jsonl
└── YYYY-MM/*.txt
```

`metadata.jsonl` contains one recommender record per URL:

- Metadata fields and synopsis
- Tags, one-line description, and intent
- Optional bounded excerpt
- `source: "meta"` or `source: "full"`
- Local file path when downloaded
- Embedding content hash

`_catalog.jsonl` contains the resumable crawl/download graph:

- URL and category
- `fetch_status: "ok"` or `"not_found"`
- Previous and next URLs
- Recommendation URLs
- Whether metadata was recorded

`source: "full"` indicates that the novel has a verified local file. It does
not mean the full body is included in the embedding input.

The legacy `data/gl_catalog.json` can be imported once:

```bash
webnovel metadata migrate-legacy-gl
```

## Reports And Monitoring

```bash
# Per-category catalogue, metadata, and downloaded counts
webnovel report catalogue

# Previous/next chain continuity
webnovel report chains --category gl --write

# Incomplete downloaded files
webnovel report incomplete \
  --urls reports/incomplete_urls.txt

# Downloaded disk usage
webnovel report size

# URL structure report for one category
webnovel report urls --category gl

# Watch a scraper run
webnovel watch --output gl
```

The watcher prints an initial size/count summary and reports every stable new
file with its title, chapter count, character count, size, source URL, integrity
status, and path.

## Windscribe

Bulk category downloads can split work between two routes:

```bash
webnovel download categories all \
  --windscribe \
  --windscribe-location "Singapore - SMRT"
```

- Direct route: newest toward oldest
- Windscribe route: oldest toward newest
- A shared queue prevents duplicate claims
- Public IPs are compared before downloading

The implementation binds sessions to the LAN and tunnel interfaces. It disables
the Windscribe firewall because that firewall blocks the interface-bound direct
route. Re-enable it after the run when required:

```bash
windscribe-cli firewall on
```

Advanced setup remains available through:

```bash
webnovel admin windscribe -- --port 8888
```

## Rate Limits And Interruptions

The scraper uses `curl-cffi` browser impersonation, response classification,
retry backoff, and delays between novels.

Use conservative worker counts. Repeated missing-page requests are more likely
to trigger rate limiting than requests for valid pages.

Metadata crawling checkpoints its JSONL files periodically, before a requested
pause, and on shutdown. Bulk downloads stop claiming new novels after a stop
request and wait for active novel downloads to finish. The TUI's `Ctrl+S` and
`/stop` use this same cooperative path.

## Development

Run the network-free workflow tests:

```bash
~/venvs/recsys/bin/python -m unittest discover -s tests -v
```

Compile-check all Python modules:

```bash
~/venvs/recsys/bin/python -m py_compile \
  scraper.py recsys/*.py webnovel_app/*.py scripts/*.py
```

The scraper currently contains 52shuku-specific HTML and URL logic. Supporting
another site should be done by moving those rules behind a site-adapter
interface while retaining the shared download, storage, recommendation, and
library workflows.

## Responsible Use

This project is intended for personal archival and offline reading. Review a
site's terms and `robots.txt`, keep request rates low, do not bypass paid or
authenticated access, and do not redistribute downloaded works without
permission.
