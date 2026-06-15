"""Interactive recommendation session.

Keeps the previous result set in context so you can refine without retyping:

  > cold CEO x bubbly intern, slow burn      # free-text query
  > like 林镜疏                                # similar-to a novel
  > more 2                                    # more like result #2
  > completed                                 # restrict to 完结 and re-run
  > tags 破镜重圆 ABO                          # tag listing
  > n 15                                      # change result count
  > help / quit
"""
from __future__ import annotations

from recsys.search import Filters, Result, SearchEngine


def _print(results, fmt) -> None:
    if not results:
        print("No matches.")
        return
    for i, res in enumerate(results, 1):
        print(fmt(i, res))
        print()


def run_repl(ctx, default_n: int = 10) -> int:
    from recsys.cli import _fmt_result

    engine: SearchEngine = ctx.engine
    n = default_n
    status: str | None = None
    last: list[Result] = []

    print("Recommendation REPL. Type 'help' for commands, 'quit' to exit.")
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue

        low = line.lower()
        if low in ("quit", "exit", "q"):
            return 0
        if low in ("help", "?"):
            print(__doc__)
            continue
        if low.startswith("n "):
            try:
                n = int(line.split()[1])
                print(f"results = {n}")
            except (ValueError, IndexError):
                print("usage: n <count>")
            continue
        if low in ("completed", "完结"):
            status = "完结"
            print("filter: 完结 only (applies to subsequent searches)")
            continue
        if low in ("any", "all", "reset"):
            status = None
            print("filter cleared")
            continue

        if low.startswith("more "):
            try:
                idx = int(line.split()[1]) - 1
            except (ValueError, IndexError):
                print("usage: more <result-number>")
                continue
            if not (0 <= idx < len(last)):
                print("no such result")
                continue
            seed = last[idx].record
            vec = engine.vector_for_url(seed.url)
            last = engine.search(vec, ref_tags=set(seed.tags),
                                 filters=Filters(status=status),
                                 exclude_urls={seed.url}, n=n)
            print(f"More like 《{seed.title}》:\n")
            _print(last, _fmt_result)
            continue

        if low.startswith("like "):
            seed = engine.resolve_title(line[5:].strip())
            if seed is None:
                print("no novel matched")
                continue
            vec = engine.vector_for_url(seed.url)
            last = engine.search(vec, ref_tags=set(seed.tags),
                                 filters=Filters(status=status),
                                 exclude_urls={seed.url}, n=n)
            print(f"Like 《{seed.title}》:\n")
            _print(last, _fmt_result)
            continue

        if low.startswith("tags "):
            wanted = {t for t in line[5:].split() if t}
            f = Filters(require_tags=wanted, status=status)
            from recsys.search import _passes
            matches = [r for r in engine.records if _passes(r, f)]
            matches.sort(key=lambda r: (len(wanted & set(r.tags)), r.year_month),
                         reverse=True)
            last = [Result(record=r, score=0.0, cosine=0.0) for r in matches[:n]]
            print(f"{len(matches)} novel(s) with {wanted}:\n")
            _print(last, _fmt_result)
            continue

        # Default: treat the line as a free-text query.
        vec = ctx.embedder.encode_query(line)
        last = engine.search(vec, filters=Filters(status=status), n=n)
        _print(last, _fmt_result)

    return 0
