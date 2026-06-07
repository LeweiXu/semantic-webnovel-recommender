#!/usr/bin/env python3
"""
scraper.py — 52shuku.net GL novel chain-walk scraper

Usage:
    python scraper.py --seed URL          # first run: scrape URL, init state
    python scraper.py --forward           # scrape newer novels (下一篇 from newest)
    python scraper.py --backward          # scrape older novels (上一篇 from oldest)
    python scraper.py --backward --limit 5
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

# ── Configuration ──────────────────────────────────────────────────────────────

OUTPUT_DIR = Path("output")
STATE_FILE = Path("state.json")
FAILED_LOG = Path("failed.log")
LOG_DIR    = Path("logs")
IMPERSONATE = "chrome136"
REQUEST_TIMEOUT = 20

DELAY_CHAPTER = 0.5        # seconds between chapter page fetches (within a novel)
DELAY_CHAPTER_JITTER = 0.15
DELAY_NOVEL = 2.0          # seconds between novels
DELAY_NOVEL_JITTER = 1.0
BACKOFF_BASE = 10.0        # first-retry wait on CHALLENGED/ERROR
BACKOFF_MAX = 60.0

GL_PREFIX = "/gl/"

# Paragraph-level ad detection: strip any <p> whose text contains these strings.
AD_PATTERNS = [
    "52书库",
    "52shuku",
    "传送门：",
    "记得收藏网址",
    "推荐给朋友",
    "如果觉得52",
]

# Matches standalone chapter header paragraphs like 第1章, 第一章, 第十二章 标题...
CHAPTER_RE = re.compile(r"^第[零一二三四五六七八九十百千万亿\d]+章")

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Response classification (mirrors probe_rate_limit.py) ──────────────────────

OK = "OK"
NOT_FOUND = "NOT_FOUND"
CHALLENGED = "CHALLENGED"
RATE_LIMITED = "RATE_LIMITED"
ERROR = "ERROR"

_HARD_BLOCK_MARKERS = ("Just a moment", "cf-browser-verification", "Attention Required")
_CONTENT_MARKERS = ("小说简介", "上一篇", "下一篇", "article-content")


def _classify(resp, exc) -> str:
    if exc is not None:
        return ERROR
    status = resp.status_code
    body = resp.text or ""
    if status == 429:
        return RATE_LIMITED
    if any(m in body for m in _CONTENT_MARKERS):
        return OK
    if status in (403, 503) or any(m in body for m in _HARD_BLOCK_MARKERS):
        return CHALLENGED
    if status == 404:
        return NOT_FOUND
    if status == 200:
        return CHALLENGED
    return ERROR


# ── HTTP fetch with retry/backoff ──────────────────────────────────────────────

def fetch(session: cffi_requests.Session, url: str, max_retries: int = 4) -> cffi_requests.Response:
    """Fetch url with exponential backoff on CHALLENGED/ERROR.

    Raises FileNotFoundError on 404, RuntimeError after max_retries exhausted.
    """
    for attempt in range(max_retries):
        resp = exc = None
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, impersonate=IMPERSONATE)
        except Exception as e:
            exc = e

        verdict = _classify(resp, exc)
        if verdict == OK:
            return resp
        if verdict == NOT_FOUND:
            raise FileNotFoundError(f"404: {url}")

        wait = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_MAX)
        log.warning(
            "Attempt %d/%d: %s for %s — waiting %.0fs",
            attempt + 1, max_retries, verdict, url, wait,
        )
        time.sleep(wait)

    raise RuntimeError(f"Failed to fetch {url} after {max_retries} retries")


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class NovelMeta:
    url: str
    title: str        # novel name (without author/status)
    author: str
    status: str       # e.g. "完结" or ""
    upload_date: str
    chapter_urls: list[str] = field(default_factory=list)
    prev_url: str | None = None    # 上一篇 — older novel
    prev_title: str | None = None
    next_url: str | None = None    # 下一篇 — newer novel
    next_title: str | None = None


@dataclass
class ScrapedNovel:
    """Result of scraping one novel."""
    meta: NovelMeta
    # Each entry: (chapter_header_or_None, body_text).
    # header is None for pre-chapter preamble text.
    chapters: list[tuple[str | None, str]]
    page_count: int      # number of fetched URL pages
    file_path: Path
    skipped: bool = False   # True if output file already existed


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _make_absolute(href: str, base: str) -> str:
    return href if href.startswith("http") else urljoin(base, href)


def _is_gl_novel_url(url: str | None) -> bool:
    """True only for /gl/... paths that are not the index page itself."""
    if not url:
        return False
    path = urlparse(url).path
    if not path.startswith(GL_PREFIX):
        return False
    tail = path[len(GL_PREFIX):].rstrip("/")
    return bool(tail) and not re.match(r"index", tail)


def _parse_h1(h1_text: str) -> tuple[str, str, str]:
    """Parse 'novel_author【status】' → (novel, author, status)."""
    text = h1_text.strip()
    status = ""
    m = re.search(r"【([^】]*)】", text)
    if m:
        status = m.group(1)
        text = text[: m.start()].strip()
    if "_" in text:
        idx = text.rfind("_")
        novel, author = text[:idx].strip(), text[idx + 1:].strip()
    else:
        novel, author = text, ""
    return novel, author, status


# ── Landing page parser ────────────────────────────────────────────────────────

def parse_landing(html: str, url: str) -> NovelMeta:
    soup = BeautifulSoup(html, "lxml")

    h1 = soup.find("h1", class_="article-title")
    h1_text = h1.get_text(strip=True) if h1 else ""
    title, author, status = _parse_h1(h1_text)

    time_tag = soup.find("time", class_="muted")
    upload_date = time_tag.get_text(strip=True) if time_tag else ""

    # Chapter links — ul.list li.mulu a; de-dup by href to drop "开始阅读" twin
    seen: set[str] = set()
    chapter_urls: list[str] = []
    for a in soup.select("ul.list li.mulu a"):
        href = a.get("href", "")
        if not href:
            continue
        abs_href = _make_absolute(href, url)
        if abs_href not in seen:
            seen.add(abs_href)
            chapter_urls.append(abs_href)

    # Navigation
    prev_url = prev_title = next_url = next_title = None
    nav = soup.find("nav", class_="article-nav")
    if nav:
        for span_class, is_prev in (("article-nav-prev", True), ("article-nav-next", False)):
            span = nav.find("span", class_=span_class)
            if not span:
                continue
            a = span.find("a")
            if not a:
                continue
            target_url = _make_absolute(a["href"], url)
            target_title = a.get_text(strip=True)
            if _is_gl_novel_url(target_url):
                if is_prev:
                    prev_url, prev_title = target_url, target_title
                else:
                    next_url, next_title = target_url, target_title

    return NovelMeta(
        url=url,
        title=title,
        author=author,
        status=status,
        upload_date=upload_date,
        chapter_urls=chapter_urls,
        prev_url=prev_url,
        prev_title=prev_title,
        next_url=next_url,
        next_title=next_title,
    )


# ── Chapter page parser ────────────────────────────────────────────────────────

def _is_ad(text: str) -> bool:
    return any(pat in text for pat in AD_PATTERNS)


def parse_chapter_page(html: str) -> str:
    """Extract cleaned paragraph text from one reading page."""
    soup = BeautifulSoup(html, "lxml")
    article = soup.find("article", class_="article-content") or soup.find(id="nr1")
    if not article:
        return ""

    paragraphs = []
    for p in article.find_all("p"):
        text = p.get_text()
        if _is_ad(text):
            continue
        stripped = text.strip()
        if stripped:
            paragraphs.append(stripped)

    return "\n\n".join(paragraphs)


def split_into_chapters(pages: list[str]) -> list[tuple[str | None, str]]:
    """Merge all page texts, then split on 第N章 paragraph markers.

    Returns [(header, body), ...] where header is the matched 第N章 line or
    None for any leading preamble before the first chapter marker.
    """
    all_text = "\n\n".join(p for p in pages if p.strip())
    paragraphs = [p.strip() for p in all_text.split("\n\n") if p.strip()]

    result: list[tuple[str | None, str]] = []
    current_header: str | None = None
    current_body: list[str] = []

    for para in paragraphs:
        if CHAPTER_RE.match(para):
            if current_body or current_header is not None:
                result.append((current_header, "\n\n".join(current_body)))
            current_header = para
            current_body = []
        else:
            current_body.append(para)

    if current_body or current_header is not None:
        result.append((current_header, "\n\n".join(current_body)))

    return result


# ── Output ─────────────────────────────────────────────────────────────────────

_FS_UNSAFE = re.compile(r'[\\/:*?"<>|【】\r\n]')
_DIVIDER = "═" * 40


def title_to_filename(title: str, author: str, status: str) -> str:
    parts = [title]
    if author:
        parts.append(author)
    if status:
        parts.append(status)
    base = "_".join(parts)
    base = _FS_UNSAFE.sub("", base).strip()
    base = re.sub(r"\s+", "_", base)
    return base[:200] + ".txt"


def write_txt(meta: NovelMeta, chapters: list[tuple[str | None, str]], out_path: Path) -> None:
    def nav_filename(nav_title: str | None) -> str:
        return "—" if not nav_title else title_to_filename(*_parse_h1(nav_title))

    chapter_count = sum(1 for h, _ in chapters if h is not None)

    lines = [
        f"标题：{meta.title}",
        f"作者：{meta.author}",
        f"状态：{meta.status}",
        f"上传时间：{meta.upload_date}",
        f"章节数：{chapter_count}",
        f"抓取时间：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        f"上一篇：{meta.prev_title or '—'}",
        f"        URL:  {meta.prev_url or '—'}",
        f"        文件: {nav_filename(meta.prev_title)}",
        "",
        f"下一篇：{meta.next_title or '—'}",
        f"        URL:  {meta.next_url or '—'}",
        f"        文件: {nav_filename(meta.next_title)}",
        "",
        f"来源：{meta.url}",
    ]

    has_markers = any(h is not None for h, _ in chapters)
    body_parts: list[str] = []

    for header, body in chapters:
        if has_markers and header is not None:
            body_parts.append(f"{_DIVIDER}\n\n{header}\n\n{body}")
        else:
            # Preamble block (before first chapter) or no markers at all
            body_parts.append(body)

    content = "\n".join(lines) + "\n\n" + "\n\n".join(body_parts) + "\n"
    out_path.write_text(content, encoding="utf-8")


# ── State ──────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Run log ────────────────────────────────────────────────────────────────────

def _nonws_chars(text: str) -> int:
    """Count non-whitespace characters (standard Chinese text length metric)."""
    return sum(1 for c in text if not c.isspace())


def open_run_log(mode: str, limit: int, output_dir: Path) -> tuple[Path, object]:
    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = LOG_DIR / f"run_{ts}.log"
    fh = path.open("w", encoding="utf-8")
    limit_str = str(limit) if limit else "∞"
    fh.write(
        f"=== Run {ts}  mode={mode}  limit={limit_str}  output={output_dir}/ ===\n\n"
    )
    fh.flush()
    return path, fh


def write_novel_log(fh, novel: ScrapedNovel, result: str) -> None:
    """Write a 2–4 line summary for one novel to the run log."""
    meta = novel.meta
    ts = datetime.now().strftime("%H:%M:%S")
    chapter_count = sum(1 for h, _ in novel.chapters if h is not None)
    has_markers = chapter_count > 0

    if novel.skipped:
        fh.write(f"{ts}  {meta.title} ({meta.author}) [{meta.status}]  SKIP\n")
        fh.write(f"          {meta.url}\n\n")
        fh.flush()
        return

    if result == "FAIL":
        fh.write(f"{ts}  FAIL  {meta.url}\n\n")
        fh.flush()
        return

    file_kb = novel.file_path.stat().st_size // 1024 if novel.file_path.exists() else 0
    if has_markers:
        chapter_info = f"{novel.page_count}p→{chapter_count}章"
    else:
        chapter_info = f"{novel.page_count}p  (无章节标记)"

    fh.write(
        f"{ts}  {meta.title} ({meta.author}) [{meta.status}]"
        f"  {chapter_info}  {file_kb}KB  {result}\n"
    )
    fh.write(f"          {meta.url}\n")

    # Character count table — 6 chapters per row
    if has_markers:
        numbered = [(h, b) for h, b in novel.chapters if h is not None]
        cols = 6
        rows = [numbered[i:i + cols] for i in range(0, len(numbered), cols)]
        for row in rows:
            parts = [f"{h}:{_nonws_chars(b)}" for h, b in row]
            fh.write("          " + "  ".join(parts) + "\n")
    else:
        total = sum(_nonws_chars(b) for _, b in novel.chapters)
        fh.write(f"          全文: {total}字\n")

    fh.write("\n")
    fh.flush()


def write_run_footer(fh, scraped: int, skipped: int, failed: int, elapsed: float) -> None:
    mins, secs = divmod(int(elapsed), 60)
    elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
    fh.write(
        f"=== Done: {scraped} scraped  {skipped} skipped  {failed} failed"
        f"  elapsed: {elapsed_str} ===\n"
    )
    fh.close()


# ── Novel orchestrator ─────────────────────────────────────────────────────────

def _log_failed(url: str, reason: str) -> None:
    with FAILED_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()}  {url}  {reason}\n")


def scrape_novel(
    session: cffi_requests.Session,
    url: str,
    output_dir: Path,
) -> ScrapedNovel | None:
    """Fetch landing page + all pages, write .txt.

    Returns ScrapedNovel on success/skip, None on unrecoverable failure.
    """
    try:
        resp = fetch(session, url)
    except FileNotFoundError:
        log.error("404 landing page: %s", url)
        _log_failed(url, "404")
        return None
    except RuntimeError as e:
        log.error("Cannot fetch landing %s: %s", url, e)
        _log_failed(url, str(e))
        return None

    meta = parse_landing(resp.text, url)
    if not meta.title:
        log.warning("Could not parse title from %s — skipping", url)
        _log_failed(url, "parse failure")
        return None

    out_path = output_dir / title_to_filename(meta.title, meta.author, meta.status)

    if out_path.exists():
        log.info("Skip (exists): %s", out_path.name)
        chapters = split_into_chapters([])
        return ScrapedNovel(meta=meta, chapters=chapters, page_count=0,
                            file_path=out_path, skipped=True)

    if not meta.chapter_urls:
        log.warning("No chapter URLs found for %s", url)
        _log_failed(url, "no chapters")
        return ScrapedNovel(meta=meta, chapters=[], page_count=0, file_path=out_path)

    log.info("Scraping '%s' — %d pages", meta.title, len(meta.chapter_urls))
    pages: list[str] = []
    for i, ch_url in enumerate(meta.chapter_urls):
        if i > 0:
            time.sleep(DELAY_CHAPTER + random.uniform(0, DELAY_CHAPTER_JITTER))
        try:
            ch_resp = fetch(session, ch_url)
            pages.append(parse_chapter_page(ch_resp.text))
        except Exception as e:
            log.error("  Page %d failed (%s): %s", i + 1, ch_url, e)
            pages.append(f"[页面获取失败: {ch_url}]")

    chapters = split_into_chapters(pages)
    chapter_count = sum(1 for h, _ in chapters if h is not None)
    write_txt(meta, chapters, out_path)

    log.info(
        "Written: %s  (%d pages → %d章  %dKB)",
        out_path.name,
        len(pages),
        chapter_count,
        out_path.stat().st_size // 1024,
    )
    return ScrapedNovel(
        meta=meta,
        chapters=chapters,
        page_count=len(pages),
        file_path=out_path,
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--seed", metavar="URL", help="Scrape URL and initialise state")
    mode.add_argument("--forward",  action="store_true", help="Scrape newer novels (下一篇 from newest)")
    mode.add_argument("--backward", action="store_true", help="Scrape older novels (上一篇 from oldest)")
    parser.add_argument("--limit",  type=int, default=0,              help="Stop after N novels (0 = unlimited)")
    parser.add_argument("--output", default=str(OUTPUT_DIR),          help="Output directory (default: output/)")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    session = cffi_requests.Session()
    state = load_state()

    # ── Seed ───────────────────────────────────────────────────────────────────
    if args.seed:
        mode_label = "seed"
        log_path, log_fh = open_run_log(mode_label, args.limit, output_dir)
        t0 = time.monotonic()

        url = args.seed.strip()
        novel = scrape_novel(session, url, output_dir)
        if novel:
            state["oldest_url"] = url
            state["newest_url"] = url
            save_state(state)
            log.info("State initialised: oldest=newest=%s", url)
            write_novel_log(log_fh, novel, "OK" if not novel.skipped else "SKIP")
            write_run_footer(log_fh, 1, 0, 0, time.monotonic() - t0)
            log.info("Run log: %s", log_path)
            return 0
        write_run_footer(log_fh, 0, 0, 1, time.monotonic() - t0)
        return 1

    # ── Chain-walk ─────────────────────────────────────────────────────────────
    if not state:
        log.error("No state.json — run with --seed <URL> first.")
        return 1

    is_forward = args.forward
    mode_label = "forward" if is_forward else "backward"
    log_path, log_fh = open_run_log(mode_label, args.limit, output_dir)
    t0 = time.monotonic()

    boundary_url = state["newest_url"] if is_forward else state["oldest_url"]
    dir_label = "下一篇" if is_forward else "上一篇"

    log.info("Chain walk %s from boundary: %s", dir_label, boundary_url)

    try:
        resp = fetch(session, boundary_url)
    except Exception as e:
        log.error("Cannot fetch boundary %s: %s", boundary_url, e)
        write_run_footer(log_fh, 0, 0, 1, time.monotonic() - t0)
        return 1

    boundary_meta = parse_landing(resp.text, boundary_url)
    current_url = boundary_meta.next_url if is_forward else boundary_meta.prev_url

    if not current_url:
        log.info("No %s from boundary — already at chain end.", dir_label)
        write_run_footer(log_fh, 0, 0, 0, time.monotonic() - t0)
        return 0

    n_scraped = n_skipped = n_failed = 0

    while current_url:
        if args.limit and (n_scraped + n_skipped) >= args.limit:
            log.info("Reached --limit %d.", args.limit)
            break

        novel = scrape_novel(session, current_url, output_dir)
        if novel is None:
            n_failed += 1
            write_novel_log(log_fh, _stub_novel(current_url), "FAIL")
            log.error("Unrecoverable failure at %s — stopping.", current_url)
            break

        if novel.skipped:
            n_skipped += 1
            write_novel_log(log_fh, novel, "SKIP")
        else:
            n_scraped += 1
            write_novel_log(log_fh, novel, "OK")

        if is_forward:
            state["newest_url"] = current_url
        else:
            state["oldest_url"] = current_url
        save_state(state)

        next_hop = novel.meta.next_url if is_forward else novel.meta.prev_url
        if not next_hop:
            log.info("No %s from %s — reached chain end.", dir_label, current_url)
            break
        current_url = next_hop

        delay = DELAY_NOVEL + random.uniform(0, DELAY_NOVEL_JITTER)
        log.info("Sleeping %.1fs before next novel…", delay)
        time.sleep(delay)

    write_run_footer(log_fh, n_scraped, n_skipped, n_failed, time.monotonic() - t0)
    log.info(
        "Done: %d scraped  %d skipped  %d failed  — log: %s",
        n_scraped, n_skipped, n_failed, log_path,
    )
    return 0


def _stub_novel(url: str) -> ScrapedNovel:
    """Minimal ScrapedNovel for logging a failure where we have no parsed data."""
    meta = NovelMeta(url=url, title="", author="", status="", upload_date="")
    return ScrapedNovel(meta=meta, chapters=[], page_count=0, file_path=Path(url))


if __name__ == "__main__":
    sys.exit(main())
