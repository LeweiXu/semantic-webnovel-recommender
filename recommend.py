#!/usr/bin/env python3
"""Semantic novel recommender and embedding-index maintenance.

Thin entry point over ``recsys.cli``. Recommendations run on local metadata
(title, tags, synopsis, optional excerpt) embedded with BAAI/bge-m3; the local
LLM is loaded only for the optional --parse/--rerank/--explain flags.

Subcommands (each has its own --help):
  sync       Extract metadata from <category>/*.txt into the store
  build      Embed the store into the index
  update     sync, then build
  like       Recommend novels similar to one you name
  query      Recommend novels from a free-text description
  tags       List novels carrying the given 内容标签
  download   Fetch the full text of a recommended novel
  repl       Interactive session (keeps the model and results loaded)

Examples:
  python recommend.py update
  python recommend.py like "Love U2" -n 15
  python recommend.py query "破镜重圆，刑侦，ABO，前任重逢" --category bl
  python recommend.py tags 破镜重圆 ABO
"""
from __future__ import annotations

from recsys.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
