"""Local, offline semantic recommender over a web-novel **metadata** corpus.

Each novel is represented by its metadata only — title, extracted tags, one-line
description, cleaned synopsis, and an optional bounded opening excerpt — never the
full body. Those fields are embedded with a local ``sentence-transformers`` model
(BAAI/bge-m3, 1024-dim dense vectors) into ``data/rec_index/``. Query commands
(``like``, ``query``, ``tags``, ``repl``) retrieve by exact cosine similarity plus
a tag-overlap (Jaccard) boost and metadata filters, with an optional local-LLM
(Qwen2.5-3B) parse / rerank / explain layer.

Because only metadata is embedded, the corpus scales far beyond what is downloaded
locally (currently ~10k records; designed for 100k+).

Public API
----------
``Embedder``      sentence-transformers wrapper (bge-m3) — encode docs/queries.
``Index``         the embedding matrix + url/hash manifest, with incremental build.
``SearchEngine``  cosine + tag-boost + filter retrieval over a loaded ``Index``.
``NovelRecord``   one metadata record; ``embed_text()`` is the embedding input.

These are exposed lazily (PEP 562): ``import recsys`` and the common
``from recsys.store import load_all`` stay cheap and never import torch /
sentence-transformers until an :class:`Embedder` is actually constructed.

See ``recsys/ARCHITECTURE.md`` for the full pipeline and design rationale.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__all__ = ["Embedder", "Index", "SearchEngine", "NovelRecord"]

# Public name -> submodule that defines it. Resolved on first access so the
# package import has no heavy side effects (torch loads only inside Embedder()).
_EXPORTS = {
    "Embedder": "recsys.embed",
    "Index": "recsys.index",
    "SearchEngine": "recsys.search",
    "NovelRecord": "recsys.store",
}


def __getattr__(name: str):  # PEP 562 module-level lazy attributes
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    obj = getattr(importlib.import_module(module), name)
    globals()[name] = obj  # cache so subsequent lookups skip the import machinery
    return obj


def __dir__():
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:  # let type checkers / IDEs see the real symbols
    from recsys.embed import Embedder
    from recsys.index import Index
    from recsys.search import SearchEngine
    from recsys.store import NovelRecord
