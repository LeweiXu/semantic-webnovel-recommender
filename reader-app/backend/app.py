"""Reader app backend.

A thin local FastAPI server that reuses the existing webnovel/recsys packages
for all file access (chapters, library, progress, downloads) and adds offline
pinyin + dictionary annotation. Run inside the ~/venvs/recsys venv so the
editable `webnovel`/`recsys`/`scraper` imports resolve.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from recsys.store import load_all
from webnovel.library import list_library

import admin_jobs
import annotate
import auth
import browse
import chapter_patterns
import dictionary
import download_manager
import novels
import recommend_api
import user_library
import user_progress
import user_settings
from auth import current_user, optional_user
from ids import nid_decode, nid_encode
from schemas import (
    BrowseListing, ChapterPatternIn, ChapterPatternOut, ChapterPatternPreviewIn,
    ChapterStub, DefineOut, NovelDetail, ProgressIn, ProgressOut, ReadingItem,
    SearchItem, ShelfItem,
)

app = FastAPI(title="Webnovel Reader", version="1.0")
app.include_router(auth.router)
app.include_router(admin_jobs.router)

# Always retain local development origins and add configured production origins.
_cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_cors_origins.extend(
    origin.strip()
    for origin in os.environ.get("NOVEL_CORS_ORIGINS", "").split(",")
    if origin.strip()
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(dict.fromkeys(_cors_origins)),
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    """Serve index.html for client-side routes such as /reader/<nid>."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)

# Annotated chapters are pure functions of their text, so cache the token lists
# (segmentation + pinyin) to make re-scrolling and revisits instant.
_ANNO_CACHE_SIZE = 60
_anno_cache: "OrderedDict[tuple[str, int, int], list]" = OrderedDict()
_anno_lock = threading.Lock()


@app.on_event("startup")
def _warm() -> None:
    # Pay jieba's model load + pypinyin's first-call cost once at startup so the
    # first real chapter isn't slow.
    annotate.tokenize("预热分词与拼音。")


def _annotated_tokens(nid: str, idx: int, body: str) -> list[dict]:
    # Include the body in the cache identity: applying a shared chapter regex
    # can change which text lives at a given chapter index.
    key = (nid, idx, hash(body))
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
def reading(username: str = Depends(current_user)) -> list[ReadingItem]:
    records = load_all()
    items: list[ReadingItem] = []
    for url, entry in user_progress.all_progress(username).items():
        record = records.get(url)
        # Raw file-explorer novels aren't in the store and key progress by their
        # browse path (not an http url); route them by that path via ``slug``.
        raw_id = url if record is None and not url.startswith(("http://", "https://")) else None
        items.append(ReadingItem(
            url=url,
            nid=nid_encode(url),
            slug=novels.slug_for(record) or raw_id,
            title=entry.get("title") or (record.title if record else url),
            author=record.author if record else "",
            category=record.category if record else "",
            position=int(entry.get("position", 0)),
            line=int(entry["line"]) if entry.get("line") is not None else None,
            total=entry.get("total"),
            updated=entry.get("updated", ""),
            tags=list(record.tags[:8]) if record else [],
            synopsis=(record.synopsis or "") if record else "",
        ))
    return items


@app.get("/api/library/search", response_model=list[SearchItem])
def search(q: str = Query(default=""), limit: int = Query(default=30, le=100)) -> list[SearchItem]:
    results = list_library(query=q, limit=limit)
    return [
        SearchItem(
            url=r.url,
            nid=nid_encode(r.url),
            slug=novels.slug_for(r) if r.downloaded else None,
            title=r.title,
            author=r.author,
            category=r.category,
            downloaded=r.downloaded,
            chapter_count=r.chapter_count,
        )
        for r in results
    ]


# ── Personal library (the explicit shelf) ────────────────────────────────────

def _describe_shelf_id(id: str) -> dict | None:
    """Resolve a shelf id to {id, url, title, kind, language}, or None.

    Handles metadata slugs, raw .txt paths, and downloadable docs (epub/pdf/
    docx), which aren't readable but can still live on the shelf.
    """
    resolved = novels.resolve_slug(id) or novels.resolve_path(id)
    if resolved is not None:
        return {
            "id": resolved.id, "url": resolved.url, "title": resolved.title,
            "kind": resolved.kind, "language": resolved.language,
        }
    try:
        path = browse.safe_join(id)
    except ValueError:
        return None
    if path.is_file() and browse.classify(path) == "doc":
        return {"id": id, "url": id, "title": path.stem, "kind": "doc", "language": ""}
    return None


def _shelf_id_for_url(url: str, records: dict) -> str | None:
    """The route id (slug or raw path) a progress url maps to, or None if it
    can't be addressed (e.g. a downloaded-then-removed metadata record)."""
    record = records.get(url)
    if record is not None:
        return novels.slug_for(record)
    if not url.startswith(("http://", "https://")):
        return url  # raw file-explorer novel keyed by its browse path
    return None


@app.get("/api/library/shelf", response_model=list[ShelfItem])
def shelf(username: str = Depends(current_user)) -> list[ShelfItem]:
    records = load_all()
    progress = user_progress.all_progress(username)
    lib = user_library.load(username)
    removed = set(lib["removed"])

    # The shelf is the explicit library, plus any novel with reading progress
    # that hasn't been explicitly removed (so a reading list is never lost).
    merged: dict[str, dict] = {id: dict(entry) for id, entry in lib["items"].items()}
    for url, prog in progress.items():
        id = _shelf_id_for_url(url, records)
        if id is None or id in removed or id in merged:
            continue
        record = records.get(url)
        merged[id] = {
            "url": url,
            "title": prog.get("title") or (record.title if record else id),
            "kind": "novel" if record else "text",
            "language": "zh",
            "added": prog.get("updated", ""),
        }

    items: list[ShelfItem] = []
    for id, entry in merged.items():
        url = entry.get("url", id)
        record = records.get(url)
        prog = progress.get(url, {})
        kind = entry.get("kind", "novel")
        # A raw file / doc lives on disk already; a "novel" is only usable once
        # its metadata record has a downloaded file (still downloading otherwise).
        downloaded = kind != "novel" or (record is not None and record.downloaded)
        items.append(ShelfItem(
            id=id,
            title=entry.get("title") or (record.title if record else id),
            author=record.author if record else "",
            category=record.category if record else "",
            kind=kind,
            language=entry.get("language", "zh"),
            downloaded=downloaded,
            url=url,
            position=int(prog.get("position", 0)),
            total=prog.get("total"),
            updated=prog.get("updated", ""),
            added=entry.get("added", ""),
            tags=list(record.tags[:8]) if record else [],
            synopsis=(record.synopsis or "") if record else "",
        ))
    # Recently read floats to the top, then recently added.
    items.sort(key=lambda it: it.updated or it.added, reverse=True)
    return items


@app.post("/api/library/shelf", response_model=list[ShelfItem])
def add_to_shelf(body: dict, username: str = Depends(current_user)) -> list[ShelfItem]:
    info = _describe_shelf_id(str(body.get("id", "")))
    if info is None:
        raise HTTPException(status_code=404, detail="No such novel")
    user_library.add(
        username, info["id"], url=info["url"], title=info["title"],
        kind=info["kind"], language=info["language"],
    )
    return shelf(username)


@app.delete("/api/library/shelf", response_model=list[ShelfItem])
def remove_from_shelf(id: str = Query(...), username: str = Depends(current_user)) -> list[ShelfItem]:
    user_library.remove(username, id)
    return shelf(username)


@app.get("/api/file/download")
def file_download(path: str = Query(...), _username: str = Depends(current_user)):
    try:
        target = browse.safe_join(path)
    except ValueError:
        raise HTTPException(status_code=404, detail="No such file")
    if not target.is_file() or browse.classify(target) != "doc":
        raise HTTPException(status_code=404, detail="No such file")
    return FileResponse(target, filename=target.name)


# ── File explorer ────────────────────────────────────────────────────────────

@app.get("/api/browse", response_model=BrowseListing)
def browse_dir(path: str = Query(default="")) -> BrowseListing:
    try:
        return BrowseListing(**browse.list_dir(path))
    except ValueError:
        raise HTTPException(status_code=404, detail="No such folder")


# ── Novel + chapters ─────────────────────────────────────────────────────────

def _resolve_or_404(nid: str):
    # ``nid`` is the frontend id, resolved in order of specificity:
    #  1. a "<category>/<stem>" metadata slug (downloaded 52shuku novels),
    #  2. a raw browse path like "GL/foo.txt" (the file explorer), or
    #  3. a legacy base64 url id (keeps old shared links working; no "/").
    resolved = novels.resolve_slug(nid)
    if resolved is None:
        resolved = novels.resolve_path(nid)
    if resolved is None and "/" not in nid:
        try:
            url = nid_decode(nid)
        except Exception:
            raise HTTPException(status_code=400, detail="Bad novel id")
        resolved = novels.resolve(url)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Novel not downloaded")
    return resolved


def _plain_tokens(body: str) -> list[dict]:
    """Untokenized tokens for non-Chinese text: whole lines, no pinyin, with the
    "\\n" paragraph-break tokens the reader splits on. Skips jieba/pypinyin."""
    tokens: list[dict] = []
    for i, line in enumerate(body.split("\n")):
        if i:
            tokens.append({"t": "\n", "py": None})
        if line:
            tokens.append({"t": line, "py": None})
    return tokens


def _pattern_result(resolved, pattern: str) -> ChapterPatternOut:
    source = novels.source_text(resolved)
    matches = chapter_patterns.matches(pattern, source)
    if len(matches) < 2:
        raise HTTPException(
            status_code=422,
            detail="That regex matches fewer than two chapter headings in this book",
        )
    if len(matches) > 10_000:
        raise HTTPException(
            status_code=422,
            detail="That regex matches too many lines; make it more specific",
        )
    return ChapterPatternOut(
        pattern=pattern,
        matches=len(matches),
        examples=matches[:5],
    )


# Chapter and progress are declared before the catch-all detail route below,
# because the ``{nid:path}`` converter is greedy and would otherwise swallow the
# ``/chapter/{idx}`` and ``/progress`` suffixes.
@app.get("/api/novel/{nid:path}/chapter/{idx}")
def chapter(nid: str, idx: int, annotate_flag: int = Query(default=1, alias="annotate")) -> JSONResponse:
    resolved = _resolve_or_404(nid)
    total = len(resolved.chapters)
    if not (0 <= idx < total):
        raise HTTPException(status_code=404, detail="Chapter out of range")
    ch = resolved.chapters[idx]
    if annotate_flag and resolved.language != "en":
        tokens = _annotated_tokens(resolved.url, idx, ch.body)  # already [{t, py}] dicts
    elif resolved.language == "en":
        # English text has no pinyin/word-lookup; skip jieba and keep paragraphs.
        tokens = _plain_tokens(ch.body)
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


@app.post(
    "/api/novel/{nid:path}/chapter-pattern/preview",
    response_model=ChapterPatternOut,
)
def preview_chapter_pattern(
    nid: str,
    body: ChapterPatternPreviewIn,
    _username: str = Depends(current_user),
) -> ChapterPatternOut:
    resolved = _resolve_or_404(nid)
    try:
        pattern = chapter_patterns.validate(body.pattern) if body.pattern.strip() else chapter_patterns.infer(body.sample)
        return _pattern_result(resolved, pattern)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put(
    "/api/novel/{nid:path}/chapter-pattern",
    response_model=ChapterPatternOut,
)
def save_chapter_pattern(
    nid: str,
    body: ChapterPatternIn,
    _username: str = Depends(current_user),
) -> ChapterPatternOut:
    resolved = _resolve_or_404(nid)
    try:
        pattern = chapter_patterns.validate(body.pattern)
        result = _pattern_result(resolved, pattern)
        chapter_patterns.set_pattern(resolved.url, pattern)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    novels.invalidate(resolved.url)
    refreshed = _resolve_or_404(nid)
    result.chapters = len(refreshed.chapters)
    selected = " ".join(body.sample.strip().splitlines()).strip()
    if selected:
        result.selected_chapter = next(
            (
                index
                for index, chapter in enumerate(refreshed.chapters)
                if chapter.title == selected
            ),
            0,
        )
    return result


@app.delete(
    "/api/novel/{nid:path}/chapter-pattern",
    response_model=ChapterPatternOut,
)
def delete_chapter_pattern(
    nid: str,
    _username: str = Depends(current_user),
) -> ChapterPatternOut:
    resolved = _resolve_or_404(nid)
    old = resolved.chapter_pattern or ""
    chapter_patterns.remove(resolved.url)
    novels.invalidate(resolved.url)
    refreshed = _resolve_or_404(nid)
    return ChapterPatternOut(
        pattern="",
        matches=0,
        chapters=len(refreshed.chapters),
        examples=[old] if old else [],
    )


@app.post("/api/novel/{nid:path}/progress", response_model=ProgressOut)
def set_progress(
    nid: str, body: ProgressIn, username: str = Depends(current_user)
) -> ProgressOut:
    resolved = _resolve_or_404(nid)
    total = len(resolved.chapters)
    position = max(0, min(body.position, max(total - 1, 0)))
    line = max(0, body.line) if body.line is not None else None
    entry = user_progress.set_position(
        username, resolved.url, position, line,
        anchor_version=body.anchor_version,
        title=resolved.title, total=total, force=body.reset,
    )
    return ProgressOut(
        ok=True,
        position=int(entry.get("position", 0)),
        line=int(entry["line"]) if entry.get("line") is not None else None,
        anchor_version=int(entry.get("anchor_version", 2)),
        updated=entry.get("updated", ""),
    )


@app.get("/api/novel/{nid:path}", response_model=NovelDetail)
def novel_detail(nid: str, username: str | None = Depends(optional_user)) -> NovelDetail:
    resolved = _resolve_or_404(nid)
    total = len(resolved.chapters)
    # Opening a novel puts it on the shelf (the explicit library is the shelf).
    if username:
        user_library.add(
            username, resolved.id, url=resolved.url, title=resolved.title,
            kind=resolved.kind, language=resolved.language,
        )
    entry = user_progress.get_entry(username, resolved.url) if username else {}
    saved = int(entry.get("position", 0))
    position = min(saved, max(total - 1, 0))
    line = int(entry["line"]) if position == saved and entry.get("line") is not None else None
    # Annotate the synopsis too (pinyin + clickable words). The client only shows
    # it when the reader turns the synopsis-pinyin setting on; cache like chapters.
    # English novels skip annotation entirely.
    synopsis_tokens = (
        _annotated_tokens(resolved.url, -1, resolved.synopsis)
        if resolved.synopsis and resolved.language != "en"
        else []
    )
    return NovelDetail(
        url=resolved.url,
        nid=nid_encode(resolved.url),
        slug=resolved.id,
        title=resolved.title,
        author=resolved.author,
        category=resolved.category,
        tags=list(resolved.tags[:12]),
        synopsis=resolved.synopsis,
        synopsis_tokens=synopsis_tokens,
        downloaded=True,
        total=total,
        position=position,
        line=line,
        anchor_version=int(entry.get("anchor_version", 1 if line is not None else 2)),
        chapters=[
            ChapterStub(index=i, title=c.title)
            for i, c in enumerate(resolved.chapters)
        ],
        kind=resolved.kind,
        language=resolved.language,
        chapter_mode=resolved.chapter_mode,
        chapter_pattern=resolved.chapter_pattern,
    )


# ── Per-user settings (stored alongside progress) ────────────────────────────

@app.get("/api/settings")
def get_settings(username: str = Depends(current_user)) -> dict:
    return user_settings.get(username)


@app.put("/api/settings")
def put_settings(body: dict, username: str = Depends(current_user)) -> dict:
    return user_settings.put(username, body)


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
    try:
        results = recommend_api.query(q, n=n, category=category)
    except Exception:
        # Free-text query needs bge-m3 (torch + sentence-transformers + a first-run
        # model download). The model-free features stay usable, so degrade softly.
        return JSONResponse({
            "results": [],
            "error": "Free-text search needs the bge-m3 model (install the full "
                     "requirements; the first query also downloads the model). "
                     "Meanwhile, click a point on the map or a result's “Similar”.",
        })
    return JSONResponse({"results": results})


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


# ── Download queue (server-side, survives client reloads) ────────────────────

@app.post("/api/download")
def download(body: dict, username: str = Depends(current_user)) -> dict:
    try:
        return download_manager.enqueue(body.get("url", ""), username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/downloads")
def downloads(username: str = Depends(current_user)) -> list[dict]:
    return download_manager.snapshot(username)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "dictionary": dictionary.ready()}


# ── Static frontend (production single-port mode) ────────────────────────────
# Mounted last so /api/* always wins. ``check_dir=False`` keeps the route
# registered even when uvicorn starts before the frontend is built; once dist/
# appears, deep-link reloads work without rebuilding the FastAPI route table.
app.mount(
    "/",
    SPAStaticFiles(directory=str(FRONTEND_DIST), html=True, check_dir=False),
    name="frontend",
)
