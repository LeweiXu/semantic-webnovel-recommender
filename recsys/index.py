"""Build / load the embedding index in data/rec_index/.

Artifacts:
  * embeddings.npy — float32 [N, D], L2-normalized, row-aligned with manifest urls
  * manifest.json  — {model, dim, created, urls: [...], hashes: {url: content_hash}}

The build is incremental: a record whose url+content_hash already appear in the
manifest reuses its existing vector; only new or changed records are re-embedded.
This keeps the constantly-growing corpus cheap to refresh.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from repo_paths import DATA_DIR  # noqa: E402

from recsys.embed import DEFAULT_MODEL, Embedder  # noqa: E402
from recsys.store import NovelRecord, load_all  # noqa: E402

INDEX_DIR = DATA_DIR / "rec_index"
EMBEDDINGS_PATH = INDEX_DIR / "embeddings.npy"
MANIFEST_PATH = INDEX_DIR / "manifest.json"


@dataclass
class Index:
    model: str
    dim: int
    urls: list[str]
    embeddings: np.ndarray          # [N, dim] float32, row-aligned with urls
    hashes: dict[str, str]          # url -> content_hash

    def row_of(self) -> dict[str, int]:
        return {u: i for i, u in enumerate(self.urls)}


def load_index() -> Index | None:
    if not (MANIFEST_PATH.exists() and EMBEDDINGS_PATH.exists()):
        return None
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    embeddings = np.load(EMBEDDINGS_PATH)
    return Index(
        model=manifest["model"],
        dim=manifest["dim"],
        urls=manifest["urls"],
        embeddings=embeddings,
        hashes=manifest["hashes"],
    )


def _save_index(model: str, dim: int, urls: list[str],
                embeddings: np.ndarray, hashes: dict[str, str]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(EMBEDDINGS_PATH, embeddings)
    manifest = {
        "model": model,
        "dim": dim,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(urls),
        "urls": urls,
        "hashes": hashes,
    }
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MANIFEST_PATH)


def build(model_name: str = DEFAULT_MODEL, device: str = "auto",
          rebuild: bool = False, batch_size: int = 32) -> dict:
    """(Re)build the index from the store. Returns a small stats dict."""
    store = load_all()
    records: list[NovelRecord] = sorted(
        store.values(), key=lambda r: (r.upload_date, r.url)
    )
    if not records:
        raise RuntimeError("Store is empty — run `sync` first.")

    old = None if rebuild else load_index()
    reusable: dict[str, np.ndarray] = {}
    if old is not None and old.model == model_name:
        row_of = old.row_of()
        for url, h in old.hashes.items():
            reusable[(url, h)] = old.embeddings[row_of[url]]  # type: ignore[index]

    urls = [r.url for r in records]
    hashes = {r.url: r.content_hash for r in records}

    to_embed: list[tuple[int, str]] = []
    cached: dict[int, np.ndarray] = {}
    for i, rec in enumerate(records):
        vec = reusable.get((rec.url, rec.content_hash))
        if vec is not None:
            cached[i] = vec
        else:
            to_embed.append((i, rec.embed_text()))

    # Determine dim without loading the model when everything is cached.
    if to_embed:
        embedder = Embedder(model_name, device=device)
        dim = embedder.dim
    else:
        dim = old.dim if old is not None else Embedder(model_name, device=device).dim

    embeddings = np.zeros((len(records), dim), dtype=np.float32)
    for i, vec in cached.items():
        embeddings[i] = vec
    if to_embed:
        new_vecs = embedder.encode_docs(
            [t for _, t in to_embed], batch_size=batch_size
        )
        for (i, _), vec in zip(to_embed, new_vecs):
            embeddings[i] = vec

    _save_index(model_name, dim, urls, embeddings, hashes)
    return {
        "total": len(records),
        "embedded": len(to_embed),
        "reused": len(cached),
        "model": model_name,
        "dim": dim,
    }
