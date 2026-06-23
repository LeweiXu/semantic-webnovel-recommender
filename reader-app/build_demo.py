#!/usr/bin/env python3
"""Build the bundled demo corpus served by the reader-app Discover page.

Selects a small, display-rich slice of gl + yanqing novels, attaches their
BAAI/bge-m3 vectors (reusing the main index for gl; embedding a fresh slice for
yanqing), computes a 2-D PCA projection for the "semantic map", and writes a
self-contained mini-index under reader-app/demo/. This lets the Discover page
showcase the recommender — similarity search, "more like this", and the map —
without the full (git-ignored) corpus.

Run with the project venv:
    ~/venvs/recsys/bin/python reader-app/build_demo.py [--per-cat 250]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from recsys.index import load_index          # noqa: E402
from recsys.store import NovelRecord, load_category  # noqa: E402

OUT = ROOT / "reader-app" / "demo"
IDX = OUT / "rec_index"


def displayable(r: NovelRecord) -> bool:
    return bool(r.synopsis and r.tags)


def completed_first_newest(records: list[NovelRecord]) -> list[NovelRecord]:
    recs = list(records)
    recs.sort(key=lambda r: r.upload_date, reverse=True)            # newest first
    recs.sort(key=lambda r: "完结" not in (r.status or ""))          # completed first (stable)
    return recs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-cat", type=int, default=250)
    args = ap.parse_args()

    index = load_index()
    if index is None:
        raise SystemExit("No main index found — run `python recommend.py build` first.")
    row_of = index.row_of()

    # gl: reuse vectors already in the main index (no model needed).
    gl = [r for r in load_category("gl").values() if displayable(r) and r.url in row_of]
    gl = completed_first_newest(gl)[: args.per_cat]
    gl_vecs = np.stack([index.embeddings[row_of[r.url]] for r in gl]).astype(np.float32)

    # yanqing: barely in the main index, so embed a fresh slice with bge-m3.
    yq = completed_first_newest(
        [r for r in load_category("yanqing").values() if displayable(r)]
    )[: args.per_cat]
    from recsys.embed import Embedder

    embedder = Embedder()
    yq_vecs = embedder.encode_docs(
        [r.embed_text() for r in yq], show_progress=False
    ).astype(np.float32)

    records = gl + yq
    embeddings = np.vstack([gl_vecs, yq_vecs]).astype(np.float32)
    urls = [r.url for r in records]

    # 2-D PCA via SVD (vectors already L2-normalized); scale each axis to [-1, 1].
    centered = embeddings - embeddings.mean(axis=0, keepdims=True)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    coords = centered @ vt[:2].T
    coords = coords / (np.abs(coords).max(axis=0, keepdims=True) + 1e-9)
    coords = np.round(coords, 4)

    IDX.mkdir(parents=True, exist_ok=True)
    np.save(IDX / "embeddings.npy", embeddings)
    manifest = {
        "model": index.model,
        "dim": int(embeddings.shape[1]),
        "count": len(urls),
        "urls": urls,
        "hashes": {r.url: r.content_hash for r in records},
        "coords": {r.url: [float(coords[i, 0]), float(coords[i, 1])]
                   for i, r in enumerate(records)},
    }
    (IDX / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    # metadata.jsonl, ordered to match the index rows. Drop the opening excerpt
    # (large, unused for display — vectors are already computed) to keep it light.
    with (OUT / "metadata.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            r.excerpt = ""
            f.write(r.to_json() + "\n")

    size = (IDX / "embeddings.npy").stat().st_size + (OUT / "metadata.jsonl").stat().st_size
    print(f"demo corpus: {len(gl)} gl + {len(yq)} yanqing = {len(records)} records, "
          f"dim {embeddings.shape[1]}, ~{size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
