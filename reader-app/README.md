# Reading Room

A local, offline reading app for the downloaded Chinese novels in this
repository. It renders novels continuously with **pinyin ruby** over each
character and **hover dictionary definitions**, remembers your place
automatically, and shares that bookmark with the `read.py` CLI.

Everything runs locally. Pinyin (pypinyin), word segmentation (jieba), and the
CC-CEDICT dictionary are all on-disk — no text is sent anywhere.

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

## Development

Two processes with hot reload:

```bash
# Terminal 1 — API
cd reader-app/backend && ~/venvs/recsys/bin/uvicorn app:app --reload --port 8000
# Terminal 2 — UI (proxies /api to :8000)
cd reader-app/frontend && npm run dev    # http://localhost:5173
```
