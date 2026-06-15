from __future__ import annotations

import difflib
from dataclasses import dataclass

from recsys.store import NovelRecord, load_all
from scraper import is_novel_landing_url


@dataclass
class Resolution:
    record: NovelRecord | None = None
    url: str | None = None
    candidates: list[NovelRecord] | None = None


def resolve_target(target: str, *, downloaded_only: bool = False) -> Resolution:
    target = target.strip()
    if target.startswith(("http://", "https://")):
        return Resolution(url=target if is_novel_landing_url(target) else None)

    records = list(load_all().values())
    if downloaded_only:
        records = [record for record in records if record.downloaded]
    query = target.casefold()

    exact = [record for record in records if record.title.casefold() == query]
    if len(exact) == 1:
        return Resolution(record=exact[0], url=exact[0].url)
    if len(exact) > 1:
        return Resolution(candidates=exact)

    partial = [record for record in records if query in record.title.casefold()]
    partial.sort(key=lambda record: (len(record.title), record.title, record.author))
    if len(partial) == 1:
        return Resolution(record=partial[0], url=partial[0].url)
    if partial:
        return Resolution(candidates=partial[:20])

    titles = [record.title for record in records]
    close = set(difflib.get_close_matches(target, titles, n=10, cutoff=0.55))
    candidates = [record for record in records if record.title in close]
    return Resolution(candidates=candidates or None)


def choose_resolution(
    target: str,
    *,
    downloaded_only: bool = False,
    interactive: bool = False,
) -> Resolution:
    resolution = resolve_target(target, downloaded_only=downloaded_only)
    candidates = resolution.candidates or []
    if not candidates or not interactive:
        return resolution

    print(f"Multiple novels match {target!r}:")
    for index, record in enumerate(candidates, 1):
        marker = "downloaded" if record.downloaded else "metadata"
        print(
            f"  {index}. 《{record.title}》 - {record.author or '?'} "
            f"[{record.category}] [{marker}]"
        )
    try:
        choice = int(input("Choose a number (blank to cancel): ").strip())
    except (ValueError, EOFError, KeyboardInterrupt):
        return Resolution(candidates=candidates)
    if 1 <= choice <= len(candidates):
        record = candidates[choice - 1]
        return Resolution(record=record, url=record.url)
    return Resolution(candidates=candidates)


def print_candidates(candidates: list[NovelRecord] | None) -> None:
    if not candidates:
        return
    print("Candidates:")
    for record in candidates:
        print(f"  《{record.title}》 - {record.author or '?'} [{record.category}]  {record.url}")
