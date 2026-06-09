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
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
BROKEN_CHAIN_LOG = Path("broken_chain.log")
INCOMPLETE_LOG = Path("incomplete.log")
LOG_DIR    = Path("logs")
IMPERSONATE = "chrome124"
REQUEST_TIMEOUT = 20

# When a 上一篇/下一篇 link 404s (novel deleted but neighbours not relinked),
# probe up to this many id steps within the SAME shard to bridge the gap. IDs
# are a GLOBAL base62 counter shared across all categories, so a gap of one
# deleted GL novel can span many id slots taken by other-category novels (we
# measured an 85-slot gap in the old /gl/b/ era). 150 covers those; the reverse-
# link verification in bridge_gap() keeps a wide window from false-bridging.
PROBE_BUDGET = 150

DELAY_CHAPTER = 0.1        # seconds between chapter page fetches (within a novel)
DELAY_CHAPTER_JITTER = 0.05
DELAY_NOVEL = 2.0          # seconds between novels
DELAY_NOVEL_JITTER = 0.5
BACKOFF_BASE = 5.0        # first-retry wait on CHALLENGED/ERROR
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

# ── Terminal progress line ─────────────────────────────────────────────────────
# The per-page progress is an in-place line on stderr (\r, no newline). The retry
# WARNINGs come from fetch() *while* that line is on screen, so without care they
# get appended to it. _progress_active tracks whether an unterminated progress
# line is showing; the logging handler below newline-terminates it (preserving
# it) before emitting, so warnings land on their own line and progress resumes
# below them.

_PROGRESS_WIDTH = 78  # characters wide before the \r clears
_progress_active = False


def _progress(msg: str, newline: bool = False) -> None:
    global _progress_active
    if newline:
        sys.stderr.write(msg + "\n")
        _progress_active = False
    else:
        sys.stderr.write(f"\r{msg:<{_PROGRESS_WIDTH}}")
        _progress_active = True
    sys.stderr.flush()


def _progress_clear(newline: bool = False) -> None:
    """Erase the in-place progress line if one is active."""
    global _progress_active
    if not newline and _progress_active:
        sys.stderr.write(f"\r{' ' * _PROGRESS_WIDTH}\r")
        sys.stderr.flush()
    _progress_active = False


# ── Logging ────────────────────────────────────────────────────────────────────

class _ProgressAwareHandler(logging.StreamHandler):
    """If a live in-place progress line is showing, drop to a fresh line first so
    the log message doesn't get appended onto it (and the progress line stays
    visible)."""
    def emit(self, record):
        global _progress_active
        if _progress_active:
            self.stream.write("\n")
            self.stream.flush()
            _progress_active = False
        super().emit(record)


_handler = _ProgressAwareHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
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

def fetch(
    session: cffi_requests.Session,
    url: str,
    max_retries: int = 4,
    timeout: float = REQUEST_TIMEOUT,
) -> cffi_requests.Response:
    """Fetch url with exponential backoff on CHALLENGED/ERROR.

    Raises FileNotFoundError on 404, RuntimeError after max_retries exhausted.
    """
    for attempt in range(max_retries):
        resp = exc = None
        try:
            resp = session.get(url, timeout=timeout, impersonate=IMPERSONATE)
        except Exception as e:
            exc = e

        verdict = _classify(resp, exc)
        if verdict == OK:
            return resp
        if verdict == NOT_FOUND:
            raise FileNotFoundError(f"404: {url}")
        if attempt + 1 == max_retries:
            break

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
    failed_pages: list[str] = field(default_factory=list)  # page URLs that failed to fetch


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


# base62 alphabet: 0-9 A-Z a-z (matches the site's id ordering, verified against
# adjacent prev/next links within a shard).
_B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_B62_IDX = {c: i for i, c in enumerate(_B62)}
_ID_IN_URL = re.compile(r"^(.*/)([0-9A-Za-z]+)\.html$")


def _b62_to_int(s: str) -> int | None:
    n = 0
    for c in s:
        if c not in _B62_IDX:
            return None
        n = n * 62 + _B62_IDX[c]
    return n


def _int_to_b62(n: int, width: int) -> str:
    if n == 0:
        s = "0"
    else:
        chars = []
        while n > 0:
            chars.append(_B62[n % 62])
            n //= 62
        s = "".join(reversed(chars))
    return s.rjust(width, "0")


def step_url_id(url: str, delta: int) -> str | None:
    """Shift the base62 id in a novel URL by delta, preserving prefix (shard)
    and id width. Returns None if the id isn't base62 or the step underflows."""
    m = _ID_IN_URL.match(url)
    if not m:
        return None
    prefix, ident = m.group(1), m.group(2)
    n = _b62_to_int(ident)
    if n is None:
        return None
    n2 = n + delta
    if n2 < 0:
        return None
    return f"{prefix}{_int_to_b62(n2, len(ident))}.html"


def _parse_h1(h1_text: str) -> tuple[str, str, str]:
    """Parse 'novel_author【status】' → (novel, author, status)."""
    text = h1_text.strip()
    status = ""
    m = re.search(r"【([^】]*)】", text)
    if m:
        status = m.group(1)
        text = text[: m.start()].strip()
    if "_" in text:
        novel, author = (part.strip() for part in text.split("_", 1))
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
_UPLOAD_DATE_RE = re.compile(r'(\d{4})年(\d{2})月')


def upload_month_dir(upload_date: str, base: Path) -> Path:
    """Return base/YYYY-MM/, creating it if needed. Falls back to base/unknown/."""
    m = _UPLOAD_DATE_RE.search(upload_date)
    subdir = f"{m.group(1)}-{m.group(2)}" if m else "unknown"
    d = base / subdir
    d.mkdir(exist_ok=True)
    return d


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


def write_txt(meta: NovelMeta, chapters: list[tuple[str | None, str]], out_path: Path,
              failed_pages: list[str] | None = None) -> None:
    def nav_filename(nav_title: str | None) -> str:
        return "—" if not nav_title else title_to_filename(*_parse_h1(nav_title))

    chapter_count = sum(1 for h, _ in chapters if h is not None)
    n_failed = len(failed_pages or [])
    integrity = "完整" if n_failed == 0 else f"缺失{n_failed}页（见正文 [页面获取失败]）"

    lines = [
        f"标题：{meta.title}",
        f"作者：{meta.author}",
        f"状态：{meta.status}",
        f"上传时间：{meta.upload_date}",
        f"章节数：{chapter_count}",
        f"完整性：{integrity}",
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


# ── Preamble patch ────────────────────────────────────────────────────────────

def update_nav_in_file(file_path: Path, direction: str, nav_title: str | None, nav_url: str | None) -> bool:
    """Overwrite the 上一篇 or 下一篇 block in an existing .txt preamble.

    direction: "prev" (上一篇) or "next" (下一篇)
    Returns True if the file was modified.
    """
    if not file_path.exists():
        return False

    label = "上一篇" if direction == "prev" else "下一篇"
    nav_file = title_to_filename(*_parse_h1(nav_title)) if nav_title else "—"
    new_title_line  = f"{label}：{nav_title or '—'}\n"
    new_url_line    = f"        URL:  {nav_url or '—'}\n"
    new_file_line   = f"        文件: {nav_file}\n"

    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = False
    for i, line in enumerate(lines):
        if line.startswith(f"{label}："):
            lines[i] = new_title_line
            if i + 1 < len(lines) and lines[i + 1].lstrip().startswith("URL:"):
                lines[i + 1] = new_url_line
            if i + 2 < len(lines) and lines[i + 2].lstrip().startswith("文件:"):
                lines[i + 2] = new_file_line
            changed = True
            break

    if changed:
        file_path.write_text("".join(lines), encoding="utf-8")
    return changed


# ── State ──────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    for path in (STATE_FILE, STATE_FILE.with_suffix(".json.bak")):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("%s is corrupt — trying backup", path.name)
                continue
    return {}


def save_state(state: dict) -> None:
    """Atomically persist state. state.json now holds the whole scraped-set and
    is rewritten after every novel, so a kill mid-write must not corrupt it:
    write to a temp file, fsync, keep the previous version as .bak, then
    os.replace() (atomic rename on the same filesystem)."""
    data = json.dumps(state, ensure_ascii=False, indent=2)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if STATE_FILE.exists():
        os.replace(STATE_FILE, STATE_FILE.with_suffix(".json.bak"))
    os.replace(tmp, STATE_FILE)


def _source_url_from_file(path: Path) -> str | None:
    """Read the 来源 (source URL) line from a scraped .txt preamble."""
    try:
        with path.open(encoding="utf-8") as f:
            for _ in range(25):
                line = f.readline()
                if not line:
                    break
                if line.startswith("来源："):
                    return line[len("来源："):].strip()
    except OSError:
        return None
    return None


def backfill_scraped(state: dict, output_dir: Path) -> None:
    """One-time migration: populate state['scraped'] (the set of all scraped
    source URLs) from existing output files. No-op once the key exists."""
    if "scraped" in state:
        return
    urls = set()
    for p in output_dir.rglob("*.txt"):
        u = _source_url_from_file(p)
        if u:
            urls.add(u)
    state["scraped"] = sorted(urls)
    save_state(state)
    log.info("Backfilled scraped-set: %d novels from existing files.", len(state["scraped"]))


# ── Run log ────────────────────────────────────────────────────────────────────

def _nonws_chars(text: str) -> int:
    """Count non-whitespace characters (standard Chinese text length metric)."""
    return sum(1 for c in text if not c.isspace())


def open_run_log(mode: str, limit: int, output_dir: Path) -> tuple[Path, object]:
    LOG_DIR.mkdir(exist_ok=True)
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d_%H-%M-%S")
    path = LOG_DIR / f"run_{ts}_{mode}.log"
    fh = path.open("w", encoding="utf-8")
    limit_str = str(limit) if limit else "∞"
    fh.write(
        f"=== Run {now.strftime('%Y-%m-%d %H:%M:%S')}  mode={mode}  "
        f"limit={limit_str}  output={output_dir}/ ===\n\n"
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


def _log_incomplete(url: str, out_path: Path, failed_pages: list[str]) -> None:
    with INCOMPLETE_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()}  {url}  {len(failed_pages)} failed page(s)  {out_path}\n")
        for u in failed_pages:
            f.write(f"    {u}\n")


def _is_complete_file(path: Path) -> bool:
    """Cheap completeness check from the preamble's 完整性 line. Files written
    before this field existed (no line) are treated as complete."""
    try:
        with path.open(encoding="utf-8") as f:
            for _ in range(25):
                line = f.readline()
                if not line:
                    break
                if line.startswith("完整性："):
                    return line.strip() == "完整性：完整"
    except OSError:
        return True
    return True


def find_incomplete(output_dir: Path) -> list[tuple[Path, str | None]]:
    """Scan output for novels containing a failed-page placeholder (ground
    truth). Returns [(file_path, source_url), ...]."""
    out: list[tuple[Path, str | None]] = []
    for p in output_dir.rglob("*.txt"):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if "页面获取失败" in text:
            out.append((p, _source_url_from_file(p)))
    return out


def _fetch_pages(
    session: cffi_requests.Session,
    chapter_urls: list[str],
    workers: int,
    verbose: bool,
) -> tuple[list[str], list[str]]:
    """Fetch all chapter pages. Returns (page_texts_in_order, failed_page_urls)."""
    total = len(chapter_urls)
    w = len(str(total))
    results: list[str] = [""] * total
    failed: list[str] = []
    lock = threading.Lock()
    completed = 0

    def _do_fetch(url: str) -> tuple[str, float, float]:
        t0 = time.monotonic()
        resp = fetch(session, url)
        fetch_ms = (time.monotonic() - t0) * 1000
        t1 = time.monotonic()
        text = parse_chapter_page(resp.text)
        parse_ms = (time.monotonic() - t1) * 1000
        return text, fetch_ms, parse_ms

    def _record(i: int, text: str, fetch_ms: float, parse_ms: float) -> None:
        nonlocal completed
        results[i] = text
        chars = _nonws_chars(text)
        with lock:
            completed += 1
            cnt = completed
        _progress(
            f"  [{cnt:>{w}}/{total}]"
            f"  fetch {fetch_ms:5.0f}ms"
            f"  parse {parse_ms:4.0f}ms"
            f"  {chars:5d}字",
            newline=verbose,
        )

    def _record_error(i: int, url: str, exc: Exception) -> None:
        nonlocal completed
        results[i] = f"[页面获取失败: {url}]"
        with lock:
            completed += 1
            failed.append(url)
        _progress_clear(verbose)
        log.error("  Page %d failed (%s): %s", i + 1, url, exc)

    if workers == 1:
        for i, url in enumerate(chapter_urls):
            if i > 0:
                time.sleep(DELAY_CHAPTER + random.uniform(0, DELAY_CHAPTER_JITTER))
            try:
                text, fetch_ms, parse_ms = _do_fetch(url)
                _record(i, text, fetch_ms, parse_ms)
            except Exception as e:
                _record_error(i, url, e)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_do_fetch, url): i for i, url in enumerate(chapter_urls)}
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    text, fetch_ms, parse_ms = fut.result()
                    _record(i, text, fetch_ms, parse_ms)
                except Exception as e:
                    _record_error(i, chapter_urls[i], e)

    return results, failed


def bridge_gap(session: cffi_requests.Session, dead_url: str, is_forward: bool) -> str | None:
    """A 上一篇/下一篇 link 404'd. Probe nearby ids in the SAME shard to find the
    next live GL novel. Backward → decrement ids; forward → increment.

    The first live candidate is then VERIFIED: its reverse-direction link (下一篇
    when walking backward, 上一篇 when walking forward) must point back to one of
    the dead pages we just probed. Because the site doesn't relink around deleted
    novels, a genuine bridge target still points into the gap — so a match proves
    we landed adjacent to the gap and can auto-continue. No match → return None so
    the caller falls back to manual --resume.
    """
    step = 1 if is_forward else -1
    dead_seen = {dead_url}
    rev_label = "上一篇" if is_forward else "下一篇"

    for k in range(1, PROBE_BUDGET + 1):
        cand = step_url_id(dead_url, step * k)
        if cand is None:
            break
        time.sleep(DELAY_CHAPTER)
        try:
            resp = fetch(session, cand)   # OK = live GL page; 404 → FileNotFoundError
        except FileNotFoundError:
            dead_seen.add(cand)
            continue
        except Exception:
            continue

        # Live candidate — verify its reverse link points back into the gap.
        meta = parse_landing(resp.text, cand)
        reverse_url = meta.prev_url if is_forward else meta.next_url
        if reverse_url in dead_seen:
            log.info("Bridge verified: %s %s → %s (a probed dead link)", cand, rev_label, reverse_url)
            return cand
        log.warning(
            "Bridge candidate %s found, but its %s (%s) is not one of the probed "
            "dead pages — not auto-continuing.", cand, rev_label, reverse_url or "—",
        )
        return None

    return None


def _log_broken_chain(reached_from: str | None, dead_url: str, direction: str) -> None:
    with BROKEN_CHAIN_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()}  direction={direction}\n")
        f.write(f"  reached_from: {reached_from or '(start)'}\n")
        f.write(f"  dead_link:    {dead_url}\n\n")


def scrape_novel(
    session: cffi_requests.Session,
    url: str,
    output_dir: Path,
    workers: int = 1,
    verbose: bool = False,
    month_subdir: bool = True,
    force: bool = False,
) -> ScrapedNovel | None:
    """Fetch landing page + all pages, write .txt.

    month_subdir: place the file under output_dir/YYYY-MM/ (the catalogue layout).
                  When False, write directly into output_dir (one-off downloads).
    force:        re-download even if the output file already exists.

    Returns ScrapedNovel on success/skip, None on unrecoverable failure.
    Raises FileNotFoundError if the landing page 404s (deleted novel / chain gap).
    """
    try:
        resp = fetch(session, url)
    except RuntimeError as e:
        log.error("Cannot fetch landing %s: %s", url, e)
        _log_failed(url, str(e))
        return None

    meta = parse_landing(resp.text, url)
    if not meta.title:
        log.warning("Could not parse title from %s — skipping", url)
        _log_failed(url, "parse failure")
        return None

    if month_subdir:
        novel_dir = upload_month_dir(meta.upload_date, output_dir)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        novel_dir = output_dir
    out_path = novel_dir / title_to_filename(meta.title, meta.author, meta.status)

    # Skip only if a COMPLETE file already exists. An incomplete file (a previous
    # run left failed-page placeholders) is re-fetched so it can be repaired.
    if out_path.exists() and not force:
        if _is_complete_file(out_path):
            log.info("Skip (exists): %s/%s", novel_dir.name, out_path.name)
            chapters = split_into_chapters([])
            return ScrapedNovel(meta=meta, chapters=chapters, page_count=0,
                                file_path=out_path, skipped=True)
        log.info("Re-fetching incomplete file: %s/%s", novel_dir.name, out_path.name)

    if not meta.chapter_urls:
        log.warning("No chapter URLs found for %s", url)
        _log_failed(url, "no chapters")
        return ScrapedNovel(meta=meta, chapters=[], page_count=0, file_path=out_path)

    total = len(meta.chapter_urls)
    log.info("Scraping '%s' — %d pages  (workers=%d)", meta.title, total, workers)
    pages, failed_pages = _fetch_pages(session, meta.chapter_urls, workers, verbose)

    chapters = split_into_chapters(pages)
    chapter_count = sum(1 for h, _ in chapters if h is not None)
    write_txt(meta, chapters, out_path, failed_pages=failed_pages)

    if failed_pages:
        _log_incomplete(url, out_path, failed_pages)

    _progress_clear(verbose)
    log.info(
        "Written: %s/%s  (%d pages → %d章  %dKB)%s",
        novel_dir.name,
        out_path.name,
        len(pages),
        chapter_count,
        out_path.stat().st_size // 1024,
        f"  ⚠ {len(failed_pages)} page(s) FAILED — incomplete" if failed_pages else "",
    )
    return ScrapedNovel(
        meta=meta,
        chapters=chapters,
        page_count=len(pages),
        file_path=out_path,
        failed_pages=failed_pages,
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--seed", metavar="URL", help="Scrape URL and initialise state")
    mode.add_argument("--forward",  action="store_true", help="Scrape newer novels (下一篇 from newest)")
    mode.add_argument("--backward", action="store_true", help="Scrape older novels (上一篇 from oldest)")
    mode.add_argument("--get", metavar="URL", help="Download a single novel from URL (no state change; "
                                                   "writes directly into --output, overwrites if present)")
    mode.add_argument("--repair", action="store_true", help="Re-download every novel under --output that has "
                                                            "failed-page placeholders (incomplete files)")
    parser.add_argument("--limit",   type=int, default=0,             help="Stop after N novels (0 = unlimited)")
    parser.add_argument("--output",  default=str(OUTPUT_DIR),         help="Output directory (default: output/)")
    parser.add_argument("--workers", type=int, default=1,             help="Parallel page fetches per novel (default 1; try 3–5)")
    parser.add_argument("--verbose", action="store_true",             help="Print each page on its own line instead of in-place")
    parser.add_argument("--resume",  metavar="URL",                   help="With --forward/--backward: start the walk AT this URL (inclusive). "
                                                                          "Use to bridge a broken chain manually after a deleted novel.")
    args = parser.parse_args()

    if args.resume and not (args.forward or args.backward):
        parser.error("--resume requires --forward or --backward")

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
        try:
            novel = scrape_novel(session, url, output_dir, workers=args.workers, verbose=args.verbose)
        except FileNotFoundError:
            log.error("Seed URL 404 (no such novel): %s", url)
            write_run_footer(log_fh, 0, 0, 1, time.monotonic() - t0)
            return 1
        if novel:
            state["oldest_url"] = url
            state["newest_url"] = url
            state["scraped"] = sorted(set(state.get("scraped", [])) | {url})
            save_state(state)
            log.info("State initialised: oldest=newest=%s", url)
            write_novel_log(log_fh, novel, "OK" if not novel.skipped else "SKIP")
            write_run_footer(log_fh, 1, 0, 0, time.monotonic() - t0)
            log.info("Run log: %s", log_path)
            return 0
        write_run_footer(log_fh, 0, 0, 1, time.monotonic() - t0)
        return 1

    # ── Single download ──────────────────────────────────────────────────────────
    if args.get:
        log_path, log_fh = open_run_log("get", args.limit, output_dir)
        t0 = time.monotonic()

        url = args.get.strip()
        try:
            novel = scrape_novel(session, url, output_dir, workers=args.workers,
                                 verbose=args.verbose, month_subdir=False, force=True)
        except FileNotFoundError:
            log.error("URL 404 (no such novel): %s", url)
            write_run_footer(log_fh, 0, 0, 1, time.monotonic() - t0)
            return 1
        if novel:
            write_novel_log(log_fh, novel, "OK")
            write_run_footer(log_fh, 1, 0, 0, time.monotonic() - t0)
            log.info("Run log: %s", log_path)
            return 0
        write_run_footer(log_fh, 0, 0, 1, time.monotonic() - t0)
        return 1

    # ── Repair incomplete novels ─────────────────────────────────────────────────
    if args.repair:
        log_path, log_fh = open_run_log("repair", args.limit, output_dir)
        t0 = time.monotonic()

        targets = find_incomplete(output_dir)
        log.info("Found %d incomplete novel(s) to repair.", len(targets))
        scraped = set(state.get("scraped", []))
        n_ok = n_fail = 0
        for path, src in targets:
            if not src:
                log.warning("Cannot repair %s — no 来源 URL in preamble", path)
                n_fail += 1
                continue
            try:
                novel = scrape_novel(session, src, output_dir, workers=args.workers,
                                     verbose=args.verbose, force=True)
            except FileNotFoundError:
                log.error("Repair: source 404 (deleted) %s", src)
                n_fail += 1
                continue
            if novel and not novel.failed_pages:
                n_ok += 1
                write_novel_log(log_fh, novel, "REPAIRED")
                scraped.add(src)
            else:
                n_fail += 1
                write_novel_log(log_fh, novel or _stub_novel(src), "STILL-PARTIAL")
            time.sleep(DELAY_NOVEL + random.uniform(0, DELAY_NOVEL_JITTER))

        if state:
            state["scraped"] = sorted(scraped)
            save_state(state)
        write_run_footer(log_fh, n_ok, 0, n_fail, time.monotonic() - t0)
        log.info("Repair done: %d fixed, %d still failing — log: %s", n_ok, n_fail, log_path)
        return 0

    # ── Chain-walk ─────────────────────────────────────────────────────────────
    if not state and not args.resume:
        log.error("No state.json — run with --seed <URL> first.")
        return 1

    # Set of every source URL we've ever scraped. Used as a loop guard: if the
    # walk revisits one (e.g. the old 2020 cluster, whose 上一篇 links cycle, or
    # simply rejoining already-scraped territory), we terminate cleanly.
    backfill_scraped(state, output_dir)
    scraped: set[str] = set(state.get("scraped", []))

    is_forward = args.forward
    mode_label = "forward" if is_forward else "backward"
    log_path, log_fh = open_run_log(mode_label, args.limit, output_dir)
    t0 = time.monotonic()
    dir_label = "下一篇" if is_forward else "上一篇"

    if args.resume:
        # Start AT the given URL (inclusive). Used to bridge a broken chain after
        # a deleted novel. We do NOT touch the old boundary's preamble.
        current_url = args.resume.strip()
        log.info("Resume %s walk at %s (inclusive)", dir_label, current_url)
    else:
        boundary_url = state["newest_url"] if is_forward else state["oldest_url"]
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

        # The boundary novel was scraped in a previous run and may have "—" for the
        # direction we're now walking. Patch it with the live nav data we just fetched.
        direction = "next" if is_forward else "prev"
        nav_title = boundary_meta.next_title if is_forward else boundary_meta.prev_title
        nav_url   = boundary_meta.next_url   if is_forward else boundary_meta.prev_url
        boundary_file = upload_month_dir(boundary_meta.upload_date, output_dir) / title_to_filename(
            boundary_meta.title, boundary_meta.author, boundary_meta.status
        )
        if update_nav_in_file(boundary_file, direction, nav_title, nav_url):
            log.info("Updated %s preamble of %s → %s", dir_label, boundary_file.name, nav_url)

    n_scraped = n_skipped = n_failed = 0
    last_good_url: str | None = None

    while current_url:
        if args.limit and (n_scraped + n_skipped) >= args.limit:
            log.info("Reached --limit %d.", args.limit)
            break

        if current_url in scraped:
            log.info("Already scraped %s — chain rejoined known territory / loop. Stopping.", current_url)
            break

        try:
            novel = scrape_novel(session, current_url, output_dir, workers=args.workers, verbose=args.verbose)
        except FileNotFoundError:
            # The link we followed points to a deleted novel — a chain gap.
            log.warning("404 (deleted novel): %s — probing ±%d in-shard…", current_url, PROBE_BUDGET)
            bridged = bridge_gap(session, current_url, is_forward)
            if bridged:
                log.warning("Bridged gap → resuming at %s", bridged)
                current_url = bridged
                continue
            n_failed += 1
            _log_broken_chain(last_good_url, current_url, dir_label)
            log.error("Broken chain at %s; in-shard bridge failed. Logged to %s.",
                      current_url, BROKEN_CHAIN_LOG)
            log.error("Find the next live novel on the site, then resume with:")
            log.error("    python scraper.py --%s --resume <URL>", mode_label)
            break

        if novel is None:
            n_failed += 1
            write_novel_log(log_fh, _stub_novel(current_url), "FAIL")
            log.error("Unrecoverable failure at %s — stopping.", current_url)
            break

        if novel.skipped:
            n_skipped += 1
            write_novel_log(log_fh, novel, "SKIP")
        elif novel.failed_pages:
            n_failed += 1
            write_novel_log(log_fh, novel, "PARTIAL")
            log.warning("Incomplete (%d failed pages): %s — logged to %s, will retry on --repair",
                        len(novel.failed_pages), current_url, INCOMPLETE_LOG)
        else:
            n_scraped += 1
            write_novel_log(log_fh, novel, "OK")

        if is_forward:
            state["newest_url"] = current_url
        else:
            state["oldest_url"] = current_url
        # Only mark COMPLETE novels as scraped. Incomplete ones stay out of the
        # set so a future pass / --repair will re-fetch them.
        if not novel.failed_pages:
            scraped.add(current_url)
            state["scraped"] = sorted(scraped)
        save_state(state)
        last_good_url = current_url

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
