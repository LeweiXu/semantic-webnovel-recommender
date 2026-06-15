"""Local, offline recommendation system over the scraped GL novel corpus.

A consolidated metadata store (data/novel_meta.jsonl) is the single source of
truth: the `sync` step extracts per-novel metadata (synopsis + tags) from the
full output/*.txt files, and a future landing-page-only scraper appends
metadata-only records to the same file. The `build` step embeds each record with
a local sentence-transformers model into data/rec_index/, and the query commands
(`like`, `query`, `tags`, `repl`) retrieve by semantic similarity + tag overlap +
metadata filters, with an optional local-LLM rerank/explain layer.
"""
