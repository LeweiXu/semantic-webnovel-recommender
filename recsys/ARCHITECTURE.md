# Architecture — the semantic recommender (`recsys/`)

A local, offline semantic search + recommendation engine over web-novel
**metadata**. Each novel is reduced to a short text record (title, tags,
synopsis, …), embedded with **BAAI/bge-m3** into a dense vector, and retrieved by
exact cosine similarity with a tag-overlap boost and metadata filters. An
optional local LLM adds natural-language query parsing, re-ranking, and
explanations. Nothing leaves the machine and no service is required.

> Only metadata is embedded — never full novel bodies — so the index scales far
> beyond what is downloaded locally (≈10k records today, designed for 100k+).

## Pipeline

```text
landing page / downloaded-file preamble
        │  recsys/crawl.py · recsys/extract.py · recsys/tags.py
        ▼
NovelRecord  { title, author, category, status, date,
               synopsis, tags[], one_liner, intent, excerpt }   recsys/store.py
        │  NovelRecord.embed_text()  → title + tags + one_liner + synopsis + excerpt[:1500]
        ▼
BAAI/bge-m3  (sentence-transformers, 1024-d, L2-normalized)      recsys/embed.py
        │  incremental: re-embed only when sha1(embed_text()) changed
        ▼
Index  { embeddings[N,1024], urls[N], hashes }  → data/rec_index/   recsys/index.py
        │  sims = embeddings @ query_vec          (exact cosine, sub-ms for 10k)
        ▼
SearchEngine.search()                                            recsys/search.py
        │  score = cosine + 0.15 · jaccard(query_tags, novel_tags)
        │  filters: status / category / chapters / year / author / required tags
        ▼
optional local LLM (Qwen2.5-3B): parse · rerank · explain        recsys/llm.py
        ▼
ranked recommendations  →  recommend.py {like, query, tags, repl}  recsys/cli.py
```

## Modules

| File | Role |
| --- | --- |
| `embed.py` | `Embedder` — sentence-transformers wrapper (bge-m3); `encode_docs`, `encode_query`. torch/ST imported lazily inside `__init__`. |
| `store.py` | `NovelRecord` dataclass + per-category `metadata.jsonl` load/upsert; `embed_text()` defines the embedding input. |
| `index.py` | `Index` dataclass + `build()` — incremental embedding (reuses vectors whose `(url, content_hash)` are unchanged); atomic write of `embeddings.npy` + `manifest.json`. |
| `search.py` | `SearchEngine` — exact cosine + tag-overlap (Jaccard) boost + metadata filters; `load`, `search`, `vector_for_url`. |
| `tags.py` | Regex extraction of structured tags / synopsis / filters from raw page text. |
| `extract.py` | `sync()` — parse downloaded `<category>/*.txt` into `NovelRecord`s. |
| `llm.py` | `LocalLLM` (Qwen2.5-3B) — `parse_query`, `rerank`, `explain`; loaded only on demand. |
| `catalog.py` | Crawl/download ledger records (`_catalog.jsonl`). |
| `crawl.py` | Metadata-only crawler (landing pages + prev/next + recommendation links). |
| `cli.py` | Full CLI (`sync`, `build`, `update`, `like`, `query`, `tags`, `download`, `repl`); `Context` lazily holds engine/embedder/LLM. |
| `repl.py` | Interactive session that keeps results loaded across refinements. |
| `routes.py` | Shared network/session utilities. |

`recommend.py` (repo root) is a thin wrapper around `recsys.cli:main`.

## Retrieval & ranking

`query <text>` embeds the text with bge-m3 and scores it against every stored
vector via a single NumPy matrix–vector product (`embeddings @ q`). `like <title>`
skips embedding entirely and reuses the seed novel's stored vector as the query.
The final score is:

```text
score = cosine_similarity  +  TAG_BOOST · jaccard(ref_tags, candidate_tags)
TAG_BOOST = 0.15
```

Metadata **filters** (status, category, chapter-count range, year/year-range,
author exclusion, required tags) are applied before truncating to the top-n.

## Optional local LLM

Loaded only when a flag requests it, and every method degrades gracefully
(returns the input unchanged on error):

- `--parse` — decode a free-text query into `(semantic_text, tags, filters)`.
- `--rerank` — listwise re-ranking of the top candidates by reading synopses.
- `--explain` — a one-line reason per recommendation.

Plain `like` / `query` / `tags` never touch the LLM.

## Incremental index

Each record stores `content_hash = sha1(embed_text())`; the index manifest maps
`url → hash`. `build()` re-embeds only records that are new or whose hash changed,
so updating a 10k-record corpus after editing a handful of novels costs a handful
of forward passes, not a full re-encode. `build --rebuild` forces a full pass.

## Data layout

```text
data/rec_index/
├── embeddings.npy     float32 [N, 1024], L2-normalized, row-aligned with urls
└── manifest.json      { model, dim, created, count, urls[], hashes{} }

<category>/                       # gl, yanqing, bl, xiandaidushi, ...
├── metadata.jsonl                # one NovelRecord per line (the embedding store)
├── _catalog.jsonl                # resumable crawl/download graph
└── YYYY-MM/title_author.txt      # downloaded full text (NOT embedded)
```

## Design rationale

- **Exact cosine, no ANN.** At ~10k–100k vectors a dense matrix–vector product is
  sub-millisecond and exact; FAISS/HNSW would add a dependency and approximation
  for no latency win at this scale. The vector matrix is the index.
- **Metadata-only embeddings.** Embedding short, information-dense metadata (not
  multi-MB bodies) keeps encoding cheap, the index small, and lets the corpus
  cover novels that were never downloaded.
- **Hybrid score.** Dense semantics handle paraphrase/intent; the tag Jaccard
  boost injects the domain's curated structured signal (内容标签) that pure
  embeddings under-weight.
- **Lazy heavy deps.** torch / sentence-transformers / Qwen load only when needed,
  so import-only consumers (e.g. the reader-app FastAPI backend importing
  `recsys.store`) and metadata-only commands stay fast.
- **Content-hash incrementality.** Makes the index cheaply maintainable against a
  live, growing corpus instead of a one-shot batch artifact.

## Public API

```python
import recsys                      # cheap; no torch yet

engine = recsys.SearchEngine.load()             # reads data/rec_index/ + metadata
hits = engine.search(engine.vector_for_url(url), n=10)   # "more like this"

emb = recsys.Embedder()            # now torch + bge-m3 load
hits = engine.search(emb.encode_query("破镜重圆，刑侦，ABO"), n=10)
```
