# Semantic Novel Recommender

A local, offline semantic search and recommendation engine for Chinese web
novels, built around the **BAAI/bge-m3** embedding model. It turns each novel's
**metadata** (synopsis plus extracted tags) into a 1024-dimensional dense vector
and retrieves by exact cosine similarity, a tag-overlap re-ranking boost, and
metadata filters, with an optional local-LLM layer that parses natural-language
queries, re-ranks, and explains results. Everything runs on-device: no API keys,
and no text leaves the machine.

It ships with a small **web app** (a paper-themed reader plus a **Discover** page)
so you can clone the repo and see the recommender working in a browser in a few
commands.

> Only metadata is embedded (never full novel bodies), so the index scales far
> beyond what is downloaded locally. The bundled demo covers a 500-novel slice
> (250 gl plus 250 yanqing); the design scales to 100k+.

## Quick start: run the demo web app

```bash
git clone https://github.com/LeweiXu/semantic-webnovel-recommender
cd semantic-webnovel-recommender

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

python run.py
```

`run.py` installs the frontend's npm packages and builds the UI on the first run,
serves the API and UI together on one local port, and opens your browser at
`http://localhost:8000`. Node.js is required for the one-time frontend build.
Options: `--port N`, `--host H`, `--no-open`, `--rebuild`.

What you get:

- A **Discover** page: type a description in any language (or tap a tag) to search
  the 500-novel demo corpus by meaning, hit **Similar** for more-like-this, and
  explore an interactive 2-D **map** of the embedding space (each dot is a novel;
  closer dots are more semantically alike).
- One-click **Download** of any result's full text into
  `library/<category>/<month>/`, then **Read** it in the annotated reader with
  pinyin ruby and a hover dictionary.

Notes:

- **Similar** and the **map** use precomputed vectors and need no model. Free-text
  search loads BAAI/bge-m3 on the first query (a few seconds, plus a one-time
  ~2 GB model download); the rest of the demo works without it.
- The demo data lives in `reader-app/demo/` (a 500-record metadata slice with
  precomputed vectors and a 2-D PCA projection). Rebuild or resize it from your
  own crawl with `python reader-app/build_demo.py --per-cat 250`.

## Highlights

- **Dense semantic retrieval** with BAAI/bge-m3 (1024-d, L2-normalized) via
  `sentence-transformers`.
- **Hybrid ranking**: cosine similarity plus a tag-overlap (Jaccard) boost plus
  metadata filters (status, category, length, year, author, required tags).
- **LLM query understanding** (optional, local Qwen2.5-3B): parse free text into
  tags and filters, listwise re-rank, and one-line explanations; loaded lazily.
- **Incremental index**: a per-record `sha1(embed_text())` means only new or
  changed novels are re-embedded; a 10k-record corpus updates in a few forward
  passes, not a full re-encode.
- **Exact cosine, no ANN**: a single NumPy matrix-vector product is
  sub-millisecond and exact at this scale; the embedding matrix is the index.
- **Local web app**: a FastAPI plus React reader with offline pinyin ruby, a hover
  dictionary, and the Discover recommender page.

## The recommender from the command line

```bash
# Describe what you want (free-text semantic query)
python recommend.py query "破镜重圆，刑侦，ABO，前任重逢"

# More like a novel you already know (reuses its stored vector, no model needed)
python recommend.py like "Love U2"

# Browse by tag, or let the local LLM parse a query and re-rank
python recommend.py tags 破镜重圆 ABO
python recommend.py query "completed yuri detective novels" --parse --rerank
```

Supported 52shuku categories: `gl yanqing bl xiandaidushi chongsheng jiakong
jiakonglishi chuanyue wuxia`.

---

## How the recommender works

The recommender is a from-scratch semantic-search stack (embeddings, retrieval,
hybrid ranking, and an optional LLM layer) with **no vector database and no hosted
API**. Everything below runs on the local machine.

```text
landing page / downloaded-file preamble
        │  crawl · extract · tag-mining (regex over 内容标签 / 搜索关键字 / 专题)
        ▼
NovelRecord { title, author, status, date, synopsis, tags[], one_liner, excerpt }
        │  embed_text() = title + tags + one_liner + synopsis + excerpt[:1500]
        ▼
BAAI/bge-m3  →  L2-normalized 1024-d dense vector        (sentence-transformers)
        │  incremental: re-embed only when sha1(embed_text()) changes
        ▼
Index  embeddings[N, 1024] + urls + hashes               →  data/rec_index/
        │  sims = embeddings @ q        (exact cosine, one matrix-vector product)
        ▼
score = cosine + 0.15 · Jaccard(query_tags, candidate_tags)   + metadata filters
        ▼
optional local LLM (Qwen2.5-3B): parse query · listwise rerank · explain
        ▼
ranked recommendations
```

### The model: BAAI/bge-m3

bge-m3 is multilingual with strong Chinese retrieval, emits 1024-d dense vectors,
and supports an 8192-token context (comfortably covering long 简介). It is loaded
through `sentence-transformers` with the GPU auto-detected; the query sequence
length is capped at 1024 tokens for several-fold faster encoding. Vectors are
L2-normalized at encode time, so a dot product is cosine similarity.

### What gets embedded

`NovelRecord.embed_text()` concatenates the title, mined tags, one-line hook,
cleaned synopsis, and at most 1500 characters of the opening excerpt. The full
downloaded body is never embedded. Embedding short, information-dense metadata
keeps encoding cheap, the index small, and lets the corpus cover novels that were
crawled but never downloaded, which is why it scales to 100k+.

### Retrieval: exact cosine, no ANN

The index is one `float32` `[N x 1024]` matrix. A query becomes a single NumPy
matrix-vector product (`embeddings @ q`), sub-millisecond and exact at 10k to 100k
rows. There is deliberately no FAISS or HNSW: at this scale an approximate index
would add a dependency and lose recall for no latency benefit. The matrix is the
index.

### Hybrid ranking

```text
score = cosine_similarity  +  0.15 · jaccard(query_tags, candidate_tags)
```

Dense vectors capture paraphrase and intent; the tag-overlap boost re-injects the
domain's curated structured signal (the site's 内容标签) that pure embeddings
under-weight. Metadata filters (status, category, chapter-count range, year or
year-range, author exclusion, required tags) are applied before truncating to the
top-n.

### Three query modes

- `query <text>`: embed free text with bge-m3, then semantic search.
- `like <title>`: reuse the seed novel's stored vector as the query; no model
  load, instant "more like this".
- `tags <...>`: pure structured filter and rank, no embedding at all.

### Optional local LLM (Qwen2.5-3B)

Loaded lazily and only when requested; every method degrades gracefully (it
returns its input unchanged on error):

- `--parse` decodes a natural-language query into `(semantic_text, tags,
  filters)` via a JSON schema.
- `--rerank` reads the top candidates' synopses and reorders them listwise.
- `--explain` adds a one-line reason to each recommendation.

Plain `like`, `query`, and `tags` never touch the LLM.

### Incremental index

Each record stores `content_hash = sha1(embed_text())`; the index manifest maps
`url` to that hash. A rebuild re-embeds only records that are new or whose hash
changed, so updating a 10k-record corpus after editing a handful of novels costs a
handful of forward passes rather than a full re-encode. `recommend.py build
--rebuild` forces a full pass.

**Full internals and design rationale: [`recsys/ARCHITECTURE.md`](recsys/ARCHITECTURE.md).**

---

## Everything else

Beyond the web app, a small set of repository-root scripts feed and surround the
recommender (`python <script>.py --help` for full options).

### Scripts

```text
recommend.py           Semantic recommendations + embedding-index maintenance  ★
run.py                 Start the web app (reader + Discover demo)               ★
scrape_metadata.py     Crawl metadata and catalogue graphs for selected categories
download.py            Download full novel text (categories, one title/URL, or repair)
report.py              Catalogue coverage, disk usage, and incomplete-file reports
read.py                Read a downloaded novel: track progress / copy chapters / launch GUI
tts.py                 Turn English TXT/EPUB novels into MP3 audio with edge-tts
```

### Typical CLI workflow

```bash
python scrape_metadata.py                  # 1. crawl metadata + catalogues
python recommend.py update                 # 2. sync downloaded files, then (re)build the index
python recommend.py query "破镜重圆，刑侦"   # 3. get recommendations (or: like / tags / repl)
python download.py novel "Love U2"         # 4. download a pick (by title or URL)
python read.py "Love U2" --gui             # 5. read it (web app, or --copy to the clipboard)
python report.py catalogue                 # coverage / disk-usage / chain reports
```

Bulk crawling and downloading can split work across two public IPs (a direct
route plus a Windscribe tunnel) with `--windscribe` to roughly double throughput;
see `--help` on `scrape_metadata.py` and `download.py`.

### Environment and install

Run everything in one virtualenv so the editable `recsys`, `scraper`, `scripts`,
and `webnovel` packages resolve. `pip install -r requirements.txt` covers the CLI,
the embedder, and the web app. For an RTX 50-series (Blackwell) GPU, install the
CUDA 12.8 PyTorch build first:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
pip install -e .
```

A generic `pip install torch` (CPU build) also works for the demo; the first
free-text query is just slower.

### Storage layout

```text
library/                          # all downloaded categories (git-ignored)
└── gl/                           # one self-contained folder per category
    ├── metadata.jsonl            # one recommender record per URL (the embedding store)
    ├── _catalog.jsonl            # resumable crawl/download graph
    └── YYYY-MM/title_author.txt   # downloaded full text (never embedded)

data/rec_index/                   # embeddings.npy + manifest.json (regenerable; git-ignored)
data/reading_progress.json        # shared reading bookmark (git-ignored)
reader-app/demo/                  # the committed 500-record demo corpus (Discover page)
```

### Development

```bash
~/venvs/recsys/bin/python -m unittest discover -s tests -v          # network-free tests
~/venvs/recsys/bin/python -m py_compile scraper.py recsys/*.py webnovel/*.py scripts/*.py *.py
```

The scraper still holds 52shuku-specific HTML and URL logic; supporting another
site means moving those rules behind a site-adapter interface while keeping the
shared download, storage, recommendation, and reading workflows.

### Responsible use

For personal archival and offline reading only. Review a site's terms and
`robots.txt`, keep request rates low, do not bypass paid or authenticated access,
and do not redistribute downloaded works without permission.
