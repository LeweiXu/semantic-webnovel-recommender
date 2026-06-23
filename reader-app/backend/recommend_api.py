"""Recommender API powering the Discover page, served from the bundled demo corpus.

reader-app/demo/ holds a small gl+yanqing slice with precomputed BAAI/bge-m3
vectors and a 2-D PCA projection (built by reader-app/build_demo.py). It loads
into a recsys `SearchEngine`, so the page can showcase the real recommender
without the full (git-ignored) corpus:

  * "more like this" and the semantic map need NO model (precomputed vectors);
  * free-text query lazily loads bge-m3 only to embed the query.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np

from recsys.index import Index
from recsys.search import Filters, Result, SearchEngine
from recsys.store import NovelRecord
from webnovel.library import local_path

from ids import nid_decode, nid_encode

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"
IDX_DIR = DEMO_DIR / "rec_index"

_engine: SearchEngine | None = None
_coords: dict[str, list[float]] = {}
_embedder = None
_lock = threading.Lock()
_embedder_lock = threading.Lock()


def available() -> bool:
    return (IDX_DIR / "manifest.json").exists() and (DEMO_DIR / "metadata.jsonl").exists()


def _load() -> tuple[SearchEngine, dict[str, list[float]]]:
    manifest = json.loads((IDX_DIR / "manifest.json").read_text(encoding="utf-8"))
    embeddings = np.load(IDX_DIR / "embeddings.npy")
    index = Index(
        model=manifest["model"], dim=manifest["dim"], urls=manifest["urls"],
        embeddings=embeddings, hashes=manifest["hashes"],
    )
    by_url: dict[str, NovelRecord] = {}
    with (DEMO_DIR / "metadata.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = NovelRecord.from_dict(json.loads(line))
                by_url[rec.url] = rec
    records = [by_url[u] for u in manifest["urls"]]
    return SearchEngine(index, records), manifest.get("coords", {})


def engine() -> SearchEngine:
    global _engine, _coords
    with _lock:
        if _engine is None:
            _engine, _coords = _load()
        return _engine


def _get_embedder():
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            from recsys.embed import Embedder
            _embedder = Embedder()  # lazily pulls in bge-m3 (heavy); query only
        return _embedder


def _downloaded(rec: NovelRecord) -> bool:
    path = local_path(rec)
    return bool(path and path.exists())


def _result_dict(res: Result) -> dict:
    rec = res.record
    return {
        "nid": nid_encode(rec.url),
        "url": rec.url,
        "title": rec.title,
        "author": rec.author,
        "category": rec.category,
        "status": rec.status,
        "tags": rec.tags[:8],
        "synopsis": (rec.synopsis or "")[:260],
        "cosine": round(res.cosine, 4),
        "similarity": max(0, min(100, round(res.cosine * 100))),
        "downloaded": _downloaded(rec),
    }


def _filters(category: str | None) -> Filters:
    return Filters(categories={category} if category else set())


def query(text: str, n: int = 12, category: str | None = None) -> list[dict]:
    eng = engine()
    vec = _get_embedder().encode_query(text)
    results = eng.search(vec, filters=_filters(category), n=n)
    return [_result_dict(r) for r in results]


def similar(nid: str, n: int = 12, category: str | None = None) -> dict:
    eng = engine()
    url = nid_decode(nid)
    try:
        vec = eng.vector_for_url(url)
    except KeyError:
        return {"seed": None, "results": []}
    seed = eng.records[eng._row_of[url]]
    results = eng.search(
        vec, ref_tags=set(seed.tags), filters=_filters(category),
        exclude_urls={url}, n=n,
    )
    return {
        "seed": {"nid": nid, "title": seed.title, "category": seed.category},
        "results": [_result_dict(r) for r in results],
    }


def map_points() -> dict:
    eng = engine()
    points = []
    for rec in eng.records:
        xy = _coords.get(rec.url)
        if not xy:
            continue
        points.append({
            "nid": nid_encode(rec.url),
            "x": xy[0],
            "y": xy[1],
            "category": rec.category,
            "title": rec.title,
            "tags": rec.tags[:3],
            "downloaded": _downloaded(rec),
        })
    cats = sorted({p["category"] for p in points})
    return {"points": points, "categories": cats, "count": len(points)}


def top_tags(limit: int = 18) -> list[dict]:
    from collections import Counter
    counter: Counter[str] = Counter()
    for rec in engine().records:
        counter.update(rec.tags)
    return [{"tag": t, "count": c} for t, c in counter.most_common(limit)]
