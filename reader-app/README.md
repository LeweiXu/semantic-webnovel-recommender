# Reading Room

A local, offline reading app for the Chinese novels in this repository. It opens
on a **Discover** page that showcases the semantic recommender — free-text
similarity search, "more like this", and an interactive 2-D embedding **map** —
and reads novels continuously with **pinyin ruby** over each character and
**hover dictionary definitions**, remembering your place and sharing that
bookmark with the `read.py` CLI.

Everything runs locally. Pinyin (pypinyin), word segmentation (jieba), and the
CC-CEDICT dictionary are all on-disk — no text is sent anywhere. The Discover
page is powered by a small bundled demo corpus (see **Demo corpus** below).

```
┌──────────────────────────────────────────────┐
│  读  哑巴美人            12% · 14/121      ⚙   │
│                                                │
│        yún chéng                               │
│        云 城，春夏之间。                         │   ← hover/click a word
│        ...                                     │     for its definition
└──────────────────────────────────────────────┘
   ▲ left: library (currently reading + search + paste-to-download)
   ▲ right: settings (pinyin, theme, text size, spacing, mode)
```

## Setup (one time)

Use the same virtualenv as the main project so the shared library imports
resolve:

```bash
~/venvs/recsys/bin/python reader-app/setup.py
```

This installs the backend extras (`fastapi`, `uvicorn`, `pypinyin`) into that
venv and runs `npm install` in `frontend/`. The CC-CEDICT dictionary is already
vendored in `reader-app/data/`, so nothing is downloaded.

## Run

```bash
~/venvs/recsys/bin/python reader-app/serve.py
```

This builds the frontend (first run), serves the API + UI on
`http://localhost:8000`, and opens your browser. On WSL2 the page opens in the
Windows browser; `localhost` is forwarded automatically.

Options: `--port N`, `--host H`, `--no-open`, `--rebuild`.

## Using it

- **Discover (the home page, or press `D`)** — semantic search over the bundled
  corpus: type a description in any language, tap a tag, or click a point on the
  embedding **map** to explore by similarity. Each result shows a cosine-similarity
  bar, tags, and a "✦ Similar" ("more like this") action. Powered by the repo's
  bge-m3 recommender (`recsys`).
- **Library (left, or press `L`)** — your currently-reading novels with progress,
  a search box over local metadata, and a field that accepts a pasted
  `52shuku.net` novel URL to download a new title on demand (live progress).
- **Settings (right, or press `S`)** — pinyin on/off, theme (paper / sepia /
  night), text size, line spacing, column width, and reading mode. Scroll mode
  is active; paginate is planned.
- **Reading** — scroll continuously; chapters load as you reach them. Click any
  word for its pinyin and definition. Your position saves automatically.
- A novel must be **downloaded** to read it. Use the library search/paste-link,
  or the CLI: `python download_catalogue.py novel "<title or URL>"`.

## How progress is shared

Chapter-level position is written to the repository's
`data/reading_progress.json` through the same helper `read.py` uses, so the two
always agree. The bookmark only advances when you genuinely scroll forward (it
never rewinds when you re-read), matching `read.py`'s "next to read" semantics.
Fine-grained scroll position within a chapter is kept in the browser only.

## Architecture

- `backend/` — FastAPI app reusing the repo's `webnovel`/`recsys`/`scraper`
  packages for chapters, library, progress, and downloads, plus offline
  annotation (`annotate.py`) and dictionary (`dictionary.py`).
- `frontend/` — Vite + React + TypeScript; Zustand for state; hand-rolled CSS
  theme (no UI framework).
- `data/cedict_ts.u8` — vendored CC-CEDICT (CC BY-SA 4.0; see
  `data/ATTRIBUTION.md`).
- `demo/` — the bundled demo corpus the Discover page serves (see below).

## Demo corpus

The Discover page is served from a small, committed corpus in `demo/` so it works
without the full (git-ignored) `library/` or a GPU:

```text
demo/metadata.jsonl              # ~500 gl + yanqing records (no opening excerpt)
demo/rec_index/embeddings.npy    # precomputed bge-m3 vectors, row-aligned
demo/rec_index/manifest.json     # model, urls, hashes + 2-D PCA coords for the map
```

`backend/recommend_api.py` loads it into a `recsys.SearchEngine`. **"Similar" and
the map need no model** (precomputed vectors); a **free-text query lazily loads
bge-m3** to embed the query, so the first search takes a few seconds. Rebuild the
corpus from your local `library/` with:

```bash
~/venvs/recsys/bin/python reader-app/build_demo.py --per-cat 250
```

## Development

Two processes with hot reload:

```bash
# Terminal 1 — API
cd reader-app/backend && ~/venvs/recsys/bin/uvicorn app:app --reload --port 8000
# Terminal 2 — UI (proxies /api to :8000)
cd reader-app/frontend && npm run dev    # http://localhost:5173
```
