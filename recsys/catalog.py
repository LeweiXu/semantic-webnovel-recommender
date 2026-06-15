"""Per-category crawl ledger: <category>/_catalog.jsonl.

This is the metadata crawler's resume state — the novel graph (prev/next +
recommendation edges) plus confirmed 404s, kept separate from the lean
recommender store (<category>/metadata.jsonl). One CatalogRecord per url lets a
re-run continue traversal without re-fetching, and skip known-dead pages.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from repo_paths import DATA_DIR, catalog_path  # noqa: E402


@dataclass
class CatalogRecord:
    url: str
    category: str = ""
    fetch_status: str = "ok"            # "ok" | "not_found"
    prev_url: str | None = None
    next_url: str | None = None
    rec_urls: list[str] = field(default_factory=list)
    has_meta: bool = False              # a metadata.jsonl record was written

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "CatalogRecord":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def load_catalog(category: str) -> dict[str, CatalogRecord]:
    path = catalog_path(category)
    out: dict[str, CatalogRecord] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = CatalogRecord.from_dict(json.loads(line))
            out[rec.url] = rec
    return out


def write_catalog(category: str, records: dict[str, CatalogRecord] | list[CatalogRecord]) -> None:
    items = list(records.values()) if isinstance(records, dict) else list(records)
    items.sort(key=lambda r: r.url)
    path = catalog_path(category)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in items:
            f.write(rec.to_json() + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def seed_gl_from_catalog(src: Path | None = None) -> int:
    """Initialise gl/_catalog.jsonl from the existing data/gl_catalog.json graph.

    Returns the number of records written. Does not overwrite an existing ledger
    entry's has_meta flag if the ledger already exists (it merges).
    """
    src = src or (DATA_DIR / "gl_catalog.json")
    if not src.exists():
        return 0
    raw = json.loads(src.read_text(encoding="utf-8"))
    existing = load_catalog("gl")
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("url"):
            continue
        url = entry["url"]
        prior = existing.get(url)
        existing[url] = CatalogRecord(
            url=url,
            category=entry.get("category") or "gl",
            fetch_status=entry.get("fetch_status") or "ok",
            prev_url=entry.get("prev_url"),
            next_url=entry.get("next_url"),
            rec_urls=list(entry.get("recommendation_urls") or []),
            has_meta=prior.has_meta if prior else False,
        )
    write_catalog("gl", existing)
    return len(existing)
