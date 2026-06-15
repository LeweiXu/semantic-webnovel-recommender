"""Command-line interface for the recommendation system.

Subcommands:
  sync     output/*.txt          -> data/novel_meta.jsonl   (incremental)
  build    data/novel_meta.jsonl -> data/rec_index/         (incremental)
  update   sync, then build
  like     recommend novels similar to one you name
  query    recommend novels from a free-text description
  tags     list novels carrying the given 内容标签
  repl     interactive session (keeps context between turns)
"""
from __future__ import annotations

import argparse
import sys

from recsys.embed import DEFAULT_MODEL
from recsys.search import Filters, Result, SearchEngine

# ── Output formatting ───────────────────────────────────────────────────────

def _fmt_result(rank: int, res: Result) -> str:
    rec = res.record
    marker = "📖" if rec.source == "full" else "📝"
    head = f"{rank:>2}. {marker} 《{rec.title}》"
    if rec.author:
        head += f" — {rec.author}"
    if rec.category:
        head += f" [{rec.category}]"
    if rec.status:
        head += f" [{rec.status}]"
    head += f"   {res.cosine * 100:.0f}%"
    lines = [head]
    if rec.tags:
        lines.append(f"      标签: {' / '.join(rec.tags)}")
    snippet = rec.synopsis.replace("\n", " ").strip()
    if snippet:
        lines.append(f"      {snippet[:110]}")
    if res.reason:
        lines.append(f"      ↳ {res.reason}")
    meta = []
    if rec.chapter_count:
        meta.append(f"{rec.chapter_count}章")
    if rec.year_month:
        meta.append(rec.year_month)
    if rec.file:
        meta.append(rec.file)
    else:
        meta.append(rec.url)
    lines.append(f"      {'  ·  '.join(meta)}")
    return "\n".join(lines)


def _print_results(results: list[Result]) -> None:
    if not results:
        print("No matches.")
        return
    for i, res in enumerate(results, 1):
        print(_fmt_result(i, res))
        print()


# ── Filters from CLI args ───────────────────────────────────────────────────

def _parse_year_range(spec: str | None) -> tuple[int | None, int | None]:
    if not spec:
        return None, None
    spec = spec.strip()
    if ".." in spec:
        lo, _, hi = spec.partition("..")
        return (int(lo) if lo else None), (int(hi) if hi else None)
    year = int(spec)
    return year, year


def _filters_from_args(args) -> Filters:
    yf, yt = _parse_year_range(getattr(args, "year", None))
    tags = set()
    if getattr(args, "tags", None):
        tags = {t.strip() for t in args.tags.split(",") if t.strip()}
    excl = set()
    if getattr(args, "exclude_author", None):
        excl = {a.strip() for a in args.exclude_author.split(",") if a.strip()}
    cats = set()
    if getattr(args, "category", None):
        cats = {c.strip() for c in args.category.split(",") if c.strip()}
    return Filters(
        status=getattr(args, "status", None),
        categories=cats,
        min_chapters=getattr(args, "min_ch", None),
        max_chapters=getattr(args, "max_ch", None),
        year_from=yf,
        year_to=yt,
        exclude_authors=excl,
        require_tags=tags,
    )


# ── Lazy heavy resources ────────────────────────────────────────────────────

class Context:
    """Holds the engine and lazily-loaded embedder/LLM, reused across REPL turns."""

    def __init__(self, model: str, device: str) -> None:
        self.model_name = model
        self.device = device
        self._engine: SearchEngine | None = None
        self._embedder = None
        self._llm = None

    @property
    def engine(self) -> SearchEngine:
        if self._engine is None:
            self._engine = SearchEngine.load()
        return self._engine

    @property
    def embedder(self):
        if self._embedder is None:
            from recsys.embed import Embedder
            print("Loading embedding model…", file=sys.stderr)
            self._embedder = Embedder(self.model_name, device=self.device)
        return self._embedder

    @property
    def llm(self):
        if self._llm is None:
            from recsys.llm import LocalLLM
            print("Loading local LLM…", file=sys.stderr)
            self._llm = LocalLLM(device=self.device)
        return self._llm


# ── Command handlers ────────────────────────────────────────────────────────

def cmd_sync(args) -> int:
    from recsys.extract import sync
    cats = ([c.strip() for c in args.category.split(",") if c.strip()]
            if getattr(args, "category", None) else None)
    added, updated, skipped = sync(limit=args.limit, rebuild=args.rebuild, categories=cats)
    print(f"sync: {added} added, {updated} updated, {skipped} unchanged-skipped")
    return 0


def cmd_build(args) -> int:
    from recsys.index import build
    stats = build(model_name=args.model, device=args.device, rebuild=args.rebuild)
    print(f"build: {stats['embedded']} embedded, {stats['reused']} reused, "
          f"{stats['total']} total  ({stats['model']}, dim={stats['dim']})")
    return 0


def cmd_update(args) -> int:
    rc = cmd_sync(args)
    return rc or cmd_build(args)


def _postprocess(ctx: Context, query_text: str, results: list[Result], args) -> list[Result]:
    if getattr(args, "rerank", False) and results:
        results = ctx.llm.rerank(query_text, results, top_k=min(20, len(results)))[:args.num]
    if getattr(args, "explain", False):
        for res in results:
            if not res.reason:
                res.reason = ctx.llm.explain(query_text, res.record)
    return results


def cmd_like(args) -> int:
    ctx = Context(args.model, args.device)
    engine = ctx.engine
    seed = engine.resolve_title(args.title)
    if seed is None:
        print(f"No novel matched '{args.title}'.")
        return 1
    print(f"Seed: 《{seed.title}》 — {seed.author} [{seed.status}]  "
          f"{'/'.join(seed.tags)}\n")
    vec = engine.vector_for_url(seed.url)
    results = engine.search(
        vec, ref_tags=set(seed.tags), filters=_filters_from_args(args),
        exclude_urls={seed.url}, n=args.num,
    )
    results = _postprocess(ctx, f"类似《{seed.title}》：{'/'.join(seed.tags)}", results, args)
    _print_results(results)
    return 0


def cmd_query(args) -> int:
    ctx = Context(args.model, args.device)
    engine = ctx.engine
    filters = _filters_from_args(args)
    ref_tags = set(filters.require_tags)
    text = args.text
    if args.parse:
        semantic, parsed_tags, parsed_filters = ctx.llm.parse_query(args.text)
        text = semantic
        ref_tags |= parsed_tags
        # CLI-given filters win over parsed ones.
        filters.status = filters.status or parsed_filters.status
        filters.min_chapters = filters.min_chapters or parsed_filters.min_chapters
        filters.year_from = filters.year_from or parsed_filters.year_from
        filters.year_to = filters.year_to or parsed_filters.year_to
        print(f"Parsed → text='{text}'  tags={ref_tags or '∅'}  "
              f"status={filters.status}\n", file=sys.stderr)
    vec = ctx.embedder.encode_query(text)
    results = engine.search(vec, ref_tags=ref_tags or None, filters=filters, n=args.num)
    results = _postprocess(ctx, args.text, results, args)
    _print_results(results)
    return 0


def cmd_tags(args) -> int:
    engine = SearchEngine.load()
    wanted = {x.strip() for t in args.tags for x in t.split(",") if x.strip()}
    cats = ({c.strip() for c in args.category.split(",") if c.strip()}
            if getattr(args, "category", None) else set())
    filters = Filters(require_tags=wanted, status=args.status, categories=cats)
    matches = [r for r in engine.records if _passes_for_tags(r, filters)]
    # Rank by tag overlap then recency.
    matches.sort(key=lambda r: (len(wanted & set(r.tags)), r.year_month), reverse=True)
    results = [Result(record=r, score=0.0, cosine=0.0) for r in matches[:args.num]]
    print(f"{len(matches)} novel(s) carry tags {wanted}\n")
    _print_results(results)
    return 0


def _passes_for_tags(rec, filters: Filters) -> bool:
    from recsys.search import _passes
    return _passes(rec, filters)


def cmd_download(args) -> int:
    """Fetch the full text of a recommended novel into its category folder and
    upgrade its store record from metadata-only to full."""
    from curl_cffi import requests as cffi_requests

    from scraper import category_dir_for_url, scrape_novel
    from recsys.extract import parse_txt
    from recsys.store import upsert_category

    target = args.target.strip()
    title = None
    if target.startswith("http"):
        url = target
    else:
        engine = SearchEngine.load()
        rec = engine.resolve_title(target)
        if rec is None:
            print(f"No novel matched '{target}'.")
            return 1
        url, title = rec.url, rec.title
        if rec.downloaded:
            print(f"Already downloaded: {rec.file}")
            return 0

    out_dir = category_dir_for_url(url)
    print(f"Downloading 《{title or url}》 → {out_dir.name}/ …")
    session = cffi_requests.Session()
    try:
        novel = scrape_novel(session, url, out_dir, workers=args.workers)
    finally:
        session.close()

    if novel is None:
        print("Download failed (see logs).")
        return 1
    if novel.failed_pages:
        print(f"⚠ Saved with {len(novel.failed_pages)} failed page(s): {novel.file_path}")

    new = parse_txt(novel.file_path, url)
    if new is not None:
        added, updated = upsert_category(new.category, [new])
        print(f"Saved: {novel.file_path}  ({new.chapter_count}章)  "
              f"store[{new.category}]: +{added} ~{updated}")
    if args.reindex:
        from recsys.index import build
        stats = build(model_name=args.model, device=args.device)
        print(f"reindex: {stats['embedded']} embedded, {stats['reused']} reused")
    else:
        print("Run `recommend.py build` to embed the new full text into the index.")
    return 0


def cmd_repl(args) -> int:
    from recsys.repl import run_repl
    return run_repl(Context(args.model, args.device), default_n=args.num)


# ── Argument parsing ────────────────────────────────────────────────────────

def _add_recommend_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("-n", "--num", type=int, default=10, help="Number of results")
    p.add_argument("--status", help="Filter by status, e.g. 完结")
    p.add_argument("--min-ch", type=int, dest="min_ch", help="Minimum chapter count")
    p.add_argument("--max-ch", type=int, dest="max_ch", help="Maximum chapter count")
    p.add_argument("--year", help="Year or range, e.g. 2024, 2020.., ..2022, 2020..2024")
    p.add_argument("--category", help="Comma-separated categories to restrict to (gl,yanqing,…)")
    p.add_argument("--tags", help="Comma-separated 内容标签 to require/boost")
    p.add_argument("--exclude-author", dest="exclude_author", help="Comma-separated authors to exclude")
    p.add_argument("--rerank", action="store_true", help="Local-LLM listwise rerank of the top candidates")
    p.add_argument("--explain", action="store_true", help="Local-LLM one-line reason per result")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Embedding model")
    p.add_argument("--device", default="auto", help="cuda | cpu | auto")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recommend", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sync", help="Extract metadata from <category>/*.txt into the store")
    p.add_argument("--limit", type=int, default=0, help="Max novels to scan per category (0 = all)")
    p.add_argument("--rebuild", action="store_true", help="Re-parse even already-known novels")
    p.add_argument("--category", help="Comma-separated categories to scan (default: all)")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("build", help="Embed the store into the index")
    p.add_argument("--rebuild", action="store_true", help="Re-embed everything from scratch")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Embedding model")
    p.add_argument("--device", default="auto", help="cuda | cpu | auto")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("update", help="sync then build")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--device", default="auto")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("like", help="Recommend novels similar to one you name")
    p.add_argument("title", help="Title (or part of it) of a novel in the corpus")
    _add_recommend_flags(p)
    p.set_defaults(func=cmd_like)

    p = sub.add_parser("query", help="Recommend novels from a free-text description")
    p.add_argument("text", help="Describe what you want to read")
    p.add_argument("--parse", action="store_true", help="Local-LLM parse query into tags/filters")
    _add_recommend_flags(p)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("tags", help="List novels carrying the given 内容标签")
    p.add_argument("tags", nargs="+", help="One or more tags")
    p.add_argument("-n", "--num", type=int, default=20)
    p.add_argument("--status", help="Filter by status, e.g. 完结")
    p.add_argument("--category", help="Comma-separated categories to restrict to")
    p.set_defaults(func=cmd_tags)

    p = sub.add_parser("download", help="Download the full text of a recommended novel")
    p.add_argument("target", help="Novel title (in the corpus) or a novel URL")
    p.add_argument("--workers", type=int, default=2, help="Parallel page fetches")
    p.add_argument("--reindex", action="store_true", help="Re-embed the index after download")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--device", default="auto")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("repl", help="Interactive session")
    p.add_argument("-n", "--num", type=int, default=10)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--device", default="auto")
    p.set_defaults(func=cmd_repl)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
