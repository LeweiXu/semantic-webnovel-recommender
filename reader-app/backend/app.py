"""Reader app backend.

A thin local FastAPI server that reuses the existing webnovel/recsys packages
for all file access (chapters, library, progress, downloads) and adds offline
pinyin + dictionary annotation. Run inside the ~/venvs/recsys venv so the
editable `webnovel`/`recsys`/`scraper` imports resolve.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from recsys.store import load_all
from webnovel import progress
from webnovel.library import list_library

import annotate
import dictionary
import novels
import recommend_api
from download_api import download_events
from ids import nid_decode, nid_encode
from schemas import (
    ChapterStub, DefineOut, NovelDetail, ProgressIn, ProgressOut,
    ReadingItem, SearchItem,
)

app = FastAPI(title="Webnovel Reader", version="1.0")

# Dev convenience: the Vite dev server (5173) calls the API on 8000.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# Annotated chapters are pure functions of their text, so cache the token lists
# (segmentation + pinyin) to make re-scrolling and revisits instant.
_ANNO_CACHE_SIZE = 60
_anno_cache: "OrderedDict[tuple[str, int], list]" = OrderedDict()
_anno_lock = threading.Lock()


@app.on_event("startup")
def _warm() -> None:
    # Pay jieba's model load + pypinyin's first-call cost once at startup so the
    # first real chapter isn't slow.
    annotate.tokenize("预热分词与拼音。")


def _annotated_tokens(nid: str, idx: int, body: str) -> list[dict]:
    key = (nid, idx)
    with _anno_lock:
        cached = _anno_cache.get(key)
        if cached is not None:
            _anno_cache.move_to_end(key)
            return cached
    tokens = annotate.tokenize(body)
    with _anno_lock:
        _anno_cache[key] = tokens
        _anno_cache.move_to_end(key)
        while len(_anno_cache) > _ANNO_CACHE_SIZE:
            _anno_cache.popitem(last=False)
    return tokens


# ── Library ──────────────────────────────────────────────────────────────────

@app.get("/api/library/reading", response_model=list[ReadingItem])
def reading() -> list[ReadingItem]:
    records = load_all()
    items: list[ReadingItem] = []
    for url, entry in progress.all_progress().items():
        record = records.get(url)
        items.append(ReadingItem(
            url=url,
            nid=nid_encode(url),
            title=entry.get("title") or (record.title if record else url),
            author=record.author if record else "",
            category=record.category if record else "",
            position=int(entry.get("position", 0)),
            total=entry.get("total"),
            updated=entry.get("updated", ""),
        ))
    return items


@app.get("/api/library/search", response_model=list[SearchItem])
def search(q: str = Query(default=""), limit: int = Query(default=30, le=100)) -> list[SearchItem]:
    results = list_library(query=q, limit=limit)
    return [
        SearchItem(
            url=r.url,
            nid=nid_encode(r.url),
            title=r.title,
            author=r.author,
            category=r.category,
            downloaded=r.downloaded,
            chapter_count=r.chapter_count,
        )
        for r in results
    ]


# ── Novel + chapters ─────────────────────────────────────────────────────────

def _resolve_or_404(nid: str):
    try:
        url = nid_decode(nid)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad novel id")
    resolved = novels.resolve(url)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Novel not downloaded")
    return resolved


@app.get("/api/novel/{nid}", response_model=NovelDetail)
def novel_detail(nid: str) -> NovelDetail:
    resolved = _resolve_or_404(nid)
    total = len(resolved.chapters)
    position = min(progress.get_position(resolved.url), max(total - 1, 0))
    return NovelDetail(
        url=resolved.url,
        nid=nid,
        title=resolved.record.title,
        author=resolved.record.author,
        category=resolved.record.category,
        synopsis=resolved.synopsis,
        downloaded=True,
        total=total,
        position=position,
        chapters=[
            ChapterStub(index=i, title=c.title)
            for i, c in enumerate(resolved.chapters)
        ],
    )


@app.get("/api/novel/{nid}/chapter/{idx}")
def chapter(nid: str, idx: int, annotate_flag: int = Query(default=1, alias="annotate")) -> JSONResponse:
    resolved = _resolve_or_404(nid)
    total = len(resolved.chapters)
    if not (0 <= idx < total):
        raise HTTPException(status_code=404, detail="Chapter out of range")
    ch = resolved.chapters[idx]
    if annotate_flag:
        tokens = _annotated_tokens(nid, idx, ch.body)  # already [{t, py}] dicts
    else:
        tokens = [{"t": ch.body, "py": None}]
    # Return the token dicts directly; validating 2k+ token models per chapter
    # is pure overhead on a hot path. Shape matches ChapterContent.
    return JSONResponse({
        "index": idx,
        "title": ch.title,
        "total": total,
        "tokens": tokens,
        "prev": idx - 1 if idx > 0 else None,
        "next": idx + 1 if idx + 1 < total else None,
    })


@app.post("/api/novel/{nid}/progress", response_model=ProgressOut)
def set_progress(nid: str, body: ProgressIn) -> ProgressOut:
    resolved = _resolve_or_404(nid)
    total = len(resolved.chapters)
    # Monotonic: never rewind below the furthest chapter already reached, so
    # re-reading backward doesn't move read.py's bookmark back.
    current = progress.get_position(resolved.url)
    target = max(0, min(body.position, max(total - 1, 0)))
    target = max(current, target)
    progress.set_position(resolved.url, target, title=resolved.record.title, total=total)
    entry = progress.all_progress().get(resolved.url, {})
    return ProgressOut(ok=True, position=target, updated=entry.get("updated", ""))


# ── Recommender (Discover page, bundled demo corpus) ─────────────────────────

@app.get("/api/discover/map")
def discover_map() -> JSONResponse:
    if not recommend_api.available():
        raise HTTPException(status_code=404, detail="Demo corpus not built")
    return JSONResponse(recommend_api.map_points())


@app.get("/api/discover/tags")
def discover_tags(limit: int = Query(default=18, le=60)) -> JSONResponse:
    if not recommend_api.available():
        raise HTTPException(status_code=404, detail="Demo corpus not built")
    return JSONResponse(recommend_api.top_tags(limit))


@app.get("/api/recommend")
def recommend(
    q: str = Query(min_length=1),
    n: int = Query(default=12, le=40),
    category: str | None = Query(default=None),
) -> JSONResponse:
    if not recommend_api.available():
        raise HTTPException(status_code=404, detail="Demo corpus not built")
    return JSONResponse({"results": recommend_api.query(q, n=n, category=category)})


@app.get("/api/similar/{nid}")
def similar(
    nid: str,
    n: int = Query(default=12, le=40),
    category: str | None = Query(default=None),
) -> JSONResponse:
    if not recommend_api.available():
        raise HTTPException(status_code=404, detail="Demo corpus not built")
    return JSONResponse(recommend_api.similar(nid, n=n, category=category))


# ── Dictionary ───────────────────────────────────────────────────────────────

@app.get("/api/define", response_model=DefineOut)
def define(word: str = Query(min_length=1)) -> DefineOut:
    return DefineOut(**dictionary.define(word))


# ── Download (SSE) ───────────────────────────────────────────────────────────

@app.post("/api/download")
def download(body: dict):
    url = body.get("url", "")
    return StreamingResponse(
        download_events(url),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "dictionary": dictionary.ready()}


# ── Static frontend (production single-port mode) ────────────────────────────
# Mounted last so /api/* always wins. Present only after `npm run build`.
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
