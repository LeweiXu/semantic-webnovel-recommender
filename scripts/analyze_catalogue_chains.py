#!/usr/bin/env python3
"""
Analyse prev/next link continuity in gl_catalog.json.

An unbroken edge requires both pages to agree:
    current.next_url == next.url
    next.prev_url == current.url

The strict reciprocal chains are merged for display when two segments share one
confirmed-404 boundary, or share one trivial non-reciprocal intermediary. These
events remain visible as annotated internal breaks. A machine-readable JSON
report can also be written with --json.

Usage:
    python scripts/analyze_catalogue_chains.py
    python scripts/analyze_catalogue_chains.py --output reports/chains.txt
    python scripts/analyze_catalogue_chains.py --json data/chains.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from repo_paths import DATA_DIR, REPORTS_DIR, resolve_data_input


def is_live(record: dict) -> bool:
    return record.get("fetch_status", "ok") == "ok"


def load_catalogue(path: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("catalogue root must be a JSON array")

    by_url: dict[str, dict] = {}
    for index, record in enumerate(raw):
        if not isinstance(record, dict) or not isinstance(record.get("url"), str):
            raise ValueError(f"catalogue entry {index} has no usable url")
        url = record["url"]
        if url in by_url:
            raise ValueError(f"duplicate catalogue URL: {url}")
        by_url[url] = record

    live = {url: record for url, record in by_url.items() if is_live(record)}
    return by_url, live


def valid_next_url(url: str, live: dict[str, dict]) -> str | None:
    target = live[url].get("next_url")
    if target in live and live[target].get("prev_url") == url:
        return target
    return None


def valid_prev_url(url: str, live: dict[str, dict]) -> str | None:
    target = live[url].get("prev_url")
    if target in live and live[target].get("next_url") == url:
        return target
    return None


def boundary(record: dict, direction: str, by_url: dict[str, dict],
             live: dict[str, dict]) -> dict:
    field = "prev_url" if direction == "prev" else "next_url"
    reverse_field = "next_url" if direction == "prev" else "prev_url"
    target = record.get(field)

    if not target:
        return {"kind": "chain_end", "target_url": None}
    if target not in by_url:
        return {"kind": "missing_from_catalogue", "target_url": target}
    if target not in live:
        return {"kind": "confirmed_404", "target_url": target}

    actual_reverse = live[target].get(reverse_field)
    if actual_reverse != record["url"]:
        return {
            "kind": "non_reciprocal",
            "target_url": target,
            "target_points_to": actual_reverse,
        }
    return {"kind": "connected", "target_url": target}


def record_summary(record: dict) -> dict:
    return {
        "url": record["url"],
        "title": record.get("title"),
        "author": record.get("author"),
        "upload_date": record.get("upload_date"),
    }


def build_chains(by_url: dict[str, dict], live: dict[str, dict]) -> list[dict]:
    assigned: set[str] = set()
    chains: list[dict] = []

    starts = sorted(
        (url for url in live if valid_prev_url(url, live) is None),
        key=lambda url: (live[url].get("upload_date") or "", url),
    )

    for start in starts:
        if start in assigned:
            continue
        members: list[str] = []
        current = start
        while current and current not in assigned:
            members.append(current)
            assigned.add(current)
            current = valid_next_url(current, live)
        chains.append(make_chain(members, False, by_url, live))

    # Anything left belongs to a reciprocal cycle. Pick a deterministic display
    # anchor, then walk until the cycle closes.
    remaining = set(live) - assigned
    while remaining:
        anchor = min(
            remaining,
            key=lambda url: (live[url].get("upload_date") or "", url),
        )
        members: list[str] = []
        current = anchor
        while current not in assigned:
            members.append(current)
            assigned.add(current)
            remaining.discard(current)
            next_url = valid_next_url(current, live)
            if next_url is None:
                break
            current = next_url
        is_cycle = bool(members and valid_next_url(members[-1], live) == members[0])
        chains.append(make_chain(members, is_cycle, by_url, live))

    chains.sort(
        key=lambda chain: (
            chain["start"].get("upload_date") or "",
            chain["start"]["url"],
        )
    )
    for index, chain in enumerate(chains, start=1):
        chain["chain_number"] = index
    return chains


def make_chain(members: list[str], is_cycle: bool, by_url: dict[str, dict],
               live: dict[str, dict]) -> dict:
    start = live[members[0]]
    end = live[members[-1]]
    return {
        "chain_number": 0,
        "novel_count": len(members),
        "is_cycle": is_cycle,
        "start": record_summary(start),
        "end": record_summary(end),
        "start_boundary": (
            {"kind": "cycle", "target_url": end["url"]}
            if is_cycle else boundary(start, "prev", by_url, live)
        ),
        "end_boundary": (
            {"kind": "cycle", "target_url": start["url"]}
            if is_cycle else boundary(end, "next", by_url, live)
        ),
        "urls": members,
    }


def merge_display_chains(chains: list[dict], by_url: dict[str, dict]) -> list[dict]:
    """Merge strict chains across one shared 404 or trivial mismatch boundary."""
    mergeable_kinds = {"confirmed_404", "non_reciprocal"}
    starts_by_key: dict[tuple[str, str], list[int]] = {}
    for index, chain in enumerate(chains):
        info = chain["start_boundary"]
        target = info.get("target_url")
        if info["kind"] in mergeable_kinds and target:
            starts_by_key.setdefault((info["kind"], target), []).append(index)

    next_chain: dict[int, int] = {}
    previous_chain: dict[int, int] = {}
    joins: dict[tuple[int, int], dict] = {}
    for index, chain in enumerate(chains):
        info = chain["end_boundary"]
        target = info.get("target_url")
        if info["kind"] not in mergeable_kinds or not target:
            continue
        candidates = starts_by_key.get((info["kind"], target), [])
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        if candidate == index or candidate in previous_chain:
            continue
        next_chain[index] = candidate
        previous_chain[candidate] = index
        target_record = by_url.get(target)
        joins[(index, candidate)] = {
            "kind": info["kind"],
            "target_url": target,
            "target": record_summary(target_record) if target_record and is_live(target_record) else None,
            "older_end": chain["end"],
            "newer_start": chains[candidate]["start"],
            "older_link": dict(info),
            "newer_link": dict(chains[candidate]["start_boundary"]),
        }

    merged: list[dict] = []
    consumed: set[int] = set()
    roots = [index for index in range(len(chains)) if index not in previous_chain]
    for root in roots:
        component: list[int] = []
        current = root
        while current not in consumed:
            component.append(current)
            consumed.add(current)
            if current not in next_chain:
                break
            current = next_chain[current]
        merged.append(make_display_chain(component, chains, joins))

    for index in range(len(chains)):
        if index not in consumed:
            merged.append(make_display_chain([index], chains, joins))

    merged.sort(
        key=lambda chain: (
            chain["start"].get("upload_date") or "",
            chain["start"]["url"],
        )
    )
    for index, chain in enumerate(merged, start=1):
        chain["chain_number"] = index
    return merged


def make_display_chain(component: list[int], chains: list[dict],
                       joins: dict[tuple[int, int], dict]) -> dict:
    parts = [chains[index] for index in component]
    internal_breaks = [
        joins[(left, right)]
        for left, right in zip(component, component[1:])
    ]
    urls = [url for part in parts for url in part["urls"]]
    return {
        "chain_number": 0,
        "novel_count": len(urls),
        "strict_chain_count": len(parts),
        "is_cycle": len(parts) == 1 and parts[0]["is_cycle"],
        "start": parts[0]["start"],
        "end": parts[-1]["end"],
        "start_boundary": parts[0]["start_boundary"],
        "end_boundary": parts[-1]["end_boundary"],
        "internal_breaks": internal_breaks,
        "urls": urls,
    }


def boundary_text(info: dict) -> str:
    labels = {
        "chain_end": "site chain end (null)",
        "missing_from_catalogue": "MISSING FROM CATALOGUE",
        "confirmed_404": "confirmed 404",
        "non_reciprocal": "NON-RECIPROCAL LINK",
        "connected": "connected",
        "cycle": "cycle",
    }
    text = labels[info["kind"]]
    if info.get("target_url"):
        text += f": {info['target_url']}"
    if "target_points_to" in info:
        text += f" (reverse link points to {info['target_points_to'] or 'null'})"
    return text


def internal_break_text(info: dict) -> str:
    if info["kind"] == "confirmed_404":
        return f"confirmed 404: {info['target_url']}"
    target = info.get("target") or {}
    title = f"  {target.get('title')}" if target.get("title") else ""
    return f"trivial non-reciprocal via {info['target_url']}{title}"


def render_report(chains: list[dict], by_url: dict[str, dict],
                  live: dict[str, dict], strict_chain_count: int | None = None,
                  title: str = "gl") -> str:
    lines: list[str] = []
    append = lines.append
    missing_boundaries = sum(
        boundary_info["kind"] == "missing_from_catalogue"
        for chain in chains
        for boundary_info in (chain["start_boundary"], chain["end_boundary"])
    )
    deleted_boundaries = sum(
        boundary_info["kind"] == "confirmed_404"
        for chain in chains
        for boundary_info in (chain["start_boundary"], chain["end_boundary"])
    )
    mismatch_boundaries = sum(
        boundary_info["kind"] == "non_reciprocal"
        for chain in chains
        for boundary_info in (chain["start_boundary"], chain["end_boundary"])
    )
    merged_404s = sum(
        info["kind"] == "confirmed_404"
        for chain in chains
        for info in chain.get("internal_breaks", [])
    )
    merged_mismatches = sum(
        info["kind"] == "non_reciprocal"
        for chain in chains
        for info in chain.get("internal_breaks", [])
    )

    append("=" * 88)
    append(f"52shuku {title} catalogue - prev/next chain analysis")
    append("=" * 88)
    append(f"Catalogue entries : {len(by_url)}")
    append(f"Live novels       : {len(live)}")
    append(f"Confirmed 404 URLs: {len(by_url) - len(live)}")
    if strict_chain_count is not None:
        append(f"Strict chains     : {strict_chain_count}")
    append(f"Displayed chains  : {len(chains)}")
    append(f"Internal breaks   : {sum(len(c.get('internal_breaks', [])) for c in chains)}")
    append(f"  merged 404s     : {merged_404s}")
    append(f"  merged mismatches: {merged_mismatches}")
    append(f"External missing boundaries: {missing_boundaries}")
    append(f"External 404 boundaries    : {deleted_boundaries}")
    append(f"External link mismatches   : {mismatch_boundaries}")
    append("")

    for chain in chains:
        start = chain["start"]
        end = chain["end"]
        cycle_label = "  [CYCLE]" if chain["is_cycle"] else ""
        append(
            f"CHAIN {chain['chain_number']:03d}  "
            f"{chain['novel_count']:5d} novel(s), "
            f"{chain.get('strict_chain_count', 1)} strict segment(s){cycle_label}"
        )
        append(f"  START  {start.get('upload_date') or '?'}  {start.get('title') or '?'}")
        append(f"         {start['url']}")
        append(f"         prev boundary: {boundary_text(chain['start_boundary'])}")
        for number, info in enumerate(chain.get("internal_breaks", []), start=1):
            append("           |")
            append(
                f"           |  BREAK {number}: after "
                f"{info['older_end']['url']}"
            )
            append(f"           |    {internal_break_text(info)}")
            append(f"           |  resumes at {info['newer_start']['url']}")
        if chain["novel_count"] > 1:
            append("           |")
            append(f"           |  {chain['novel_count'] - 2} other/intermediate novel(s)")
            append("           v")
        append(f"  END    {end.get('upload_date') or '?'}  {end.get('title') or '?'}")
        append(f"         {end['url']}")
        append(f"         next boundary: {boundary_text(chain['end_boundary'])}")
        append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "catalogue",
        nargs="?",
        default=str(DATA_DIR / "gl_catalog.json"),
        help="Catalogue JSON to analyse (default: data/gl_catalog.json)",
    )
    parser.add_argument(
        "--output",
        default=str(REPORTS_DIR / "gl_catalog_chains.txt"),
        help="Visual report path (default: reports/gl_catalog_chains.txt)",
    )
    parser.add_argument(
        "--json",
        default=str(DATA_DIR / "gl_catalog_chains.json"),
        help="Structured report path (default: data/gl_catalog_chains.json)",
    )
    args = parser.parse_args()

    try:
        by_url, live = load_catalogue(resolve_data_input(args.catalogue))
        strict_chains = build_chains(by_url, live)
        chains = merge_display_chains(strict_chains, by_url)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    report = render_report(chains, by_url, live, strict_chain_count=len(strict_chains))
    print(report)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report + "\n", encoding="utf-8")
    json_path = Path(args.json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(chains, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
