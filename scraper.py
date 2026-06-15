#!/usr/bin/env python3
"""Shared parsing, fetching, output, and logging for 52shuku scrapers."""
from __future__ import annotations

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
from typing import NamedTuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

# ── Configuration ──────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
# Each category lives in its own folder at the repo root (gl/ is the former
# output/). OUTPUT_DIR keeps its name as the default for the GL-only scrapers.
OUTPUT_DIR = REPO_ROOT / "gl"
LOG_DIR = REPO_ROOT / "logs"
FAILED_LOG = LOG_DIR / "failed.log"
INCOMPLETE_LOG = LOG_DIR / "incomplete.log"
IMPERSONATE = "chrome124"
REQUEST_TIMEOUT = 20

DELAY_CHAPTER = 0.1        # seconds between chapter page fetches (within a novel)
DELAY_CHAPTER_JITTER = 0.05
DELAY_NOVEL = 2.0          # seconds between novels
DELAY_NOVEL_JITTER = 0.5
BACKOFF_BASE = 5.0        # first-retry wait on CHALLENGED/ERROR
BACKOFF_MAX = 60.0

GL_PREFIX = "/gl/"
SUPPORTED_CATEGORIES = {
    "gl", "yanqing", "bl", "xiandaidushi", "chongsheng",
    "jiakong", "jiakonglishi", "chuanyue", "wuxia",
}

# The site's canonical domain. Reading pages must live here; a ul.list href that
# points anywhere else is an upstream typo (the site has been seen emitting a
# garbled domain like "https://www.52shuk_2.html/" in place of a real page link).
SITE_DOMAIN = "52shuku.net"

# When rebuilding a novel's reading-page sequence by probing _2, _3, …, stop
# after this many consecutive 404s (tolerates a small gap, then concludes the
# novel has ended).
CHAPTER_PROBE_STOP = 4

# Placeholder written into the body for a page whose fetch failed; also the
# ground-truth marker find_incomplete() scans for.
PAGE_FAIL_MARK = "[页面获取失败"

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
_progress_lock = threading.Lock()
_file_log_lock = threading.Lock()
_progress_callback = None


def set_progress_callback(callback) -> None:
    """Redirect transient page progress to an application-owned log pane."""
    global _progress_callback
    _progress_callback = callback


def _progress(msg: str, newline: bool = False) -> None:
    global _progress_active
    if _progress_callback is not None:
        _progress_callback(msg.strip())
        return
    with _progress_lock:
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
    if _progress_callback is not None:
        _progress_active = False
        return
    with _progress_lock:
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
        with _progress_lock:
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
    page_texts: list[str] = field(default_factory=list)    # parsed text per page, in order
    page_has_br: list[bool] = field(default_factory=list)  # page used <br> content layout


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _make_absolute(href: str, base: str) -> str:
    return href if href.startswith("http") else urljoin(base, href)


def category_of_url(url: str) -> str | None:
    """First path segment of a novel URL, e.g. /yanqing/02_b/x.html -> 'yanqing'."""
    segs = [s for s in urlparse(url).path.split("/") if s]
    return segs[0] if segs else None


def category_dir_for_url(url: str) -> Path:
    """The repo-root folder a novel from this URL belongs in (gl/, yanqing/, …).

    Falls back to OUTPUT_DIR (gl/) when the URL has no category segment.
    """
    cat = category_of_url(url)
    return REPO_ROOT / cat if cat else OUTPUT_DIR


def is_novel_landing_url(
    url: str | None,
    categories: set[str] | None = None,
) -> bool:
    """True for a supported 52shuku novel landing page."""
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not _same_site(url):
        return False
    segs = [segment for segment in parsed.path.split("/") if segment]
    if len(segs) < 2 or not segs[-1].endswith(".html"):
        return False
    allowed = categories if categories is not None else SUPPORTED_CATEGORIES
    if segs[0] not in allowed:
        return False
    name = segs[-1][:-5]
    return bool(name) and not name.startswith("index") and not re.search(r"_\d+$", name)


def _is_gl_novel_url(url: str | None) -> bool:
    """Backward-compatible GL-only landing-page check."""
    return is_novel_landing_url(url, {"gl"})


def _same_site(url: str) -> bool:
    """True if url's host is SITE_DOMAIN or a subdomain of it."""
    host = urlparse(url).netloc.lower().rsplit("@", 1)[-1].split(":", 1)[0]
    return host == SITE_DOMAIN or host.endswith("." + SITE_DOMAIN)


def _reading_base_path(landing_url: str) -> str | None:
    """The landing path without its '.html', i.e. the stem reading pages extend.

    e.g. https://www.52shuku.net/gl/huk.html -> /gl/huk
    Returns None for a landing URL that doesn't end in '.html'.
    """
    path = urlparse(landing_url).path
    if not path.endswith(".html"):
        return None
    return path[: -len(".html")]


def _chapter_url_ok(landing_url: str, chapter_url: str) -> bool:
    """True if chapter_url is a well-formed reading page of landing_url: same
    site and path '{landing-stem}_{N}.html'. A different/garbled domain or a
    path that doesn't extend the landing stem (an upstream typo) is rejected."""
    base_path = _reading_base_path(landing_url)
    if base_path is None:
        return True  # nothing to validate the chapter link against
    if not _same_site(chapter_url):
        return False
    pattern = re.escape(base_path) + r"_\d+\.html$"
    return bool(re.match(pattern, urlparse(chapter_url).path))


def chapter_urls_valid(landing_url: str, chapter_urls: list[str]) -> bool:
    """True if every listed reading-page URL is a well-formed page of this
    novel. A single malformed href makes the whole list untrustworthy."""
    return all(_chapter_url_ok(landing_url, u) for u in chapter_urls)


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
            current_category = category_of_url(url)
            allowed = {current_category} if current_category else None
            if is_novel_landing_url(target_url, allowed):
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


class PageContent(NamedTuple):
    text: str
    has_br: bool   # content used <br> line breaks (older layout), ad block aside


def parse_chapter_page(html: str) -> PageContent:
    """Extract cleaned paragraph text from one reading page.

    Handles three layouts seen across the site's history, including mixes of
    them on a single page:
      * newer pages wrap each paragraph in <p>;
      * older pages have no <p> at all — lines are separated by <br>;
      * some pages mix loose <br>-separated text with a few <p> blocks.
    Both <p> boundaries and <br> are treated as paragraph breaks, while inline
    markup inside a paragraph is kept together. Injected <script>/<style> ad
    nodes (which older pages place *inside* #nr1) are dropped first.

    has_br reports whether the *content* (not the trailing ad block, which
    always carries one <br>) relied on <br> line breaks, so callers can flag
    those pages.
    """
    soup = BeautifulSoup(html, "lxml")
    article = soup.find("article", class_="article-content") or soup.find(id="nr1")
    if not article:
        return PageContent("", False)

    # Drop injected scripts/styles, the page-chrome (pager + back-to-top
    # button), and the boilerplate ad paragraph — the old <p>-only parser
    # skipped these by construction. Removing the ad here also keeps its <br>
    # out of the has_br signal below.
    for tag in article.find_all(["script", "style", "button"]):
        tag.decompose()
    for tag in article.select("[class*='paginat'], .go_top"):
        tag.decompose()
    for p in article.find_all("p"):
        if _is_ad(p.get_text()):
            p.decompose()

    has_br = article.find("br") is not None

    # <br> → hard line break; a trailing break after each <p> so adjacent
    # paragraphs don't merge once we flatten with get_text().
    for br in article.find_all("br"):
        br.replace_with("\n")
    for p in article.find_all("p"):
        p.append("\n")

    paragraphs = []
    for line in article.get_text().split("\n"):
        stripped = line.strip()
        if stripped and not _is_ad(stripped):
            paragraphs.append(stripped)

    return PageContent("\n\n".join(paragraphs), has_br)


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


# ── File helpers ───────────────────────────────────────────────────────────────

def source_url_from_file(path: Path) -> str | None:
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


def write_novel_log(fh, novel: ScrapedNovel, result: str, chapter_logging: bool = False) -> None:
    """Write a summary for one novel to the run log.

    By default each page's character count is listed (so a silently empty or
    failed page shows up as 0 / ✗ instead of being hidden inside a chapter
    total). Pass chapter_logging=True for the older per-chapter table.
    """
    meta = novel.meta
    ts = datetime.now().strftime("%H:%M:%S")

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

    if chapter_logging:
        _write_chapter_table(fh, novel, result, ts, file_kb)
    else:
        _write_page_counts(fh, novel, result, ts, file_kb)


def _write_page_counts(fh, novel: ScrapedNovel, result: str, ts: str, file_kb: int) -> None:
    """Per-page character counts; '*' marks pages whose body used <br>."""
    meta = novel.meta
    cells: list[str] = []
    total = n_empty = n_failed = 0
    for i, text in enumerate(novel.page_texts):
        if text.startswith(PAGE_FAIL_MARK):
            cells.append("✗")
            n_failed += 1
            continue
        chars = _nonws_chars(text)
        total += chars
        if chars == 0:
            n_empty += 1
        br = novel.page_has_br[i] if i < len(novel.page_has_br) else False
        cells.append(f"{chars}{'*' if br else ''}")

    flags = []
    if n_failed:
        flags.append(f"{n_failed}页失败")
    if n_empty:
        flags.append(f"{n_empty}页空白")
    flag_str = ("  ⚠ " + " ".join(flags)) if flags else ""

    fh.write(
        f"{ts}  {meta.title} ({meta.author}) [{meta.status}]"
        f"  {novel.page_count}p  {total}字  {file_kb}KB  {result}{flag_str}\n"
    )
    fh.write(f"          {meta.url}\n")
    if cells:
        fh.write("          页字数 (* = <br> 版式):\n")
        cols = 12
        for r in range(0, len(cells), cols):
            row = "  ".join(f"{c:>6}" for c in cells[r:r + cols])
            fh.write(f"            {row}\n")
    fh.write("\n")
    fh.flush()


def _write_chapter_table(fh, novel: ScrapedNovel, result: str, ts: str, file_kb: int) -> None:
    """Legacy per-chapter character table (--chapter-logging)."""
    meta = novel.meta
    chapter_count = sum(1 for h, _ in novel.chapters if h is not None)
    has_markers = chapter_count > 0
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
    LOG_DIR.mkdir(exist_ok=True)
    with _file_log_lock:
        with FAILED_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()}  {url}  {reason}\n")


def _log_incomplete(url: str, out_path: Path, failed_pages: list[str]) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    with _file_log_lock:
        with INCOMPLETE_LOG.open("a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now().isoformat()}  {url}  "
                f"{len(failed_pages)} failed page(s)  {out_path}\n"
            )
            for u in failed_pages:
                f.write(f"    {u}\n")


def is_complete_file(path: Path) -> bool:
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
            out.append((p, source_url_from_file(p)))
    return out


def _fetch_pages(
    session: cffi_requests.Session,
    chapter_urls: list[str],
    workers: int,
    verbose: bool,
) -> tuple[list[str], list[str]]:
    """Fetch all chapter pages.

    Returns (page_texts_in_order, failed_page_urls, page_has_br_in_order).
    """
    total = len(chapter_urls)
    w = len(str(total))
    results: list[str] = [""] * total
    has_br: list[bool] = [False] * total
    failed: list[str] = []
    lock = threading.Lock()
    completed = 0

    def _do_fetch(url: str) -> tuple[str, bool, float, float]:
        t0 = time.monotonic()
        resp = fetch(session, url)
        fetch_ms = (time.monotonic() - t0) * 1000
        t1 = time.monotonic()
        page = parse_chapter_page(resp.text)
        parse_ms = (time.monotonic() - t1) * 1000
        return page.text, page.has_br, fetch_ms, parse_ms

    def _record(i: int, text: str, br: bool, fetch_ms: float, parse_ms: float) -> None:
        nonlocal completed
        results[i] = text
        has_br[i] = br
        chars = _nonws_chars(text)
        with lock:
            completed += 1
            cnt = completed
        _progress(
            f"  [{cnt:>{w}}/{total}]"
            f"  fetch {fetch_ms:5.0f}ms"
            f"  parse {parse_ms:4.0f}ms"
            f"  {chars:5d}字{'*' if br else ' '}",
            newline=verbose,
        )

    def _record_error(i: int, url: str, exc: Exception) -> None:
        nonlocal completed
        results[i] = f"{PAGE_FAIL_MARK}: {url}]"
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
                text, br, fetch_ms, parse_ms = _do_fetch(url)
                _record(i, text, br, fetch_ms, parse_ms)
            except Exception as e:
                _record_error(i, url, e)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_do_fetch, url): i for i, url in enumerate(chapter_urls)}
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    text, br, fetch_ms, parse_ms = fut.result()
                    _record(i, text, br, fetch_ms, parse_ms)
                except Exception as e:
                    _record_error(i, chapter_urls[i], e)

    return results, failed, has_br


def rebuild_chapter_urls(
    session: cffi_requests.Session,
    landing_url: str,
    max_pages: int = 2000,
) -> list[str]:
    """Reconstruct a novel's reading-page list when the landing page's ul.list
    links are malformed (an upstream bug).

    Probes {landing-stem}_2.html, _3.html, … in order, keeping every page that
    exists and stopping once CHAPTER_PROBE_STOP consecutive pages 404 (a single
    mid-run 404 is treated as a gap and skipped). Returns the discovered page
    URLs in order; empty if the landing URL has no usable stem or no page
    responds.
    """
    base = _reading_base_path(landing_url)
    if base is None:
        return []
    # Build candidates from the full landing URL so scheme/host are canonical.
    url_stem = landing_url[: -len(".html")]

    urls: list[str] = []
    consecutive_missing = 0
    n = 2
    while consecutive_missing < CHAPTER_PROBE_STOP and (n - 2) < max_pages:
        if n > 2:
            time.sleep(DELAY_CHAPTER + random.uniform(0, DELAY_CHAPTER_JITTER))
        candidate = f"{url_stem}_{n}.html"
        try:
            fetch(session, candidate)
        except FileNotFoundError:
            consecutive_missing += 1
        except RuntimeError as exc:
            # Couldn't get a definitive verdict (server hung up after retries);
            # stop here rather than guess where the novel ends.
            log.warning("Stopping reading-page probe at %s: %s", candidate, exc)
            break
        else:
            urls.append(candidate)
            consecutive_missing = 0
        n += 1
    return urls


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
        if is_complete_file(out_path):
            log.info("Skip (exists): %s/%s", novel_dir.name, out_path.name)
            chapters = split_into_chapters([])
            return ScrapedNovel(meta=meta, chapters=chapters, page_count=0,
                                file_path=out_path, skipped=True)
        log.info("Re-fetching incomplete file: %s/%s", novel_dir.name, out_path.name)

    # Guard against the site listing a malformed reading-page link in ul.list
    # (e.g. a garbled domain). If any listed page isn't a well-formed page of
    # this novel, discard the list and rebuild it by probing _2, _3, ….
    if meta.chapter_urls and not chapter_urls_valid(url, meta.chapter_urls):
        bad = next(u for u in meta.chapter_urls if not _chapter_url_ok(url, u))
        log.warning("Malformed reading-page link on %s (e.g. %s) — rebuilding by probing", url, bad)
        meta.chapter_urls = rebuild_chapter_urls(session, url)
        if meta.chapter_urls:
            log.info("Rebuilt %d reading-page URL(s) for %s by probing", len(meta.chapter_urls), url)
        else:
            log.warning("Could not rebuild reading-page URLs for %s by probing", url)

    if not meta.chapter_urls:
        log.warning("No chapter URLs found for %s", url)
        _log_failed(url, "no chapters")
        return ScrapedNovel(meta=meta, chapters=[], page_count=0, file_path=out_path)

    total = len(meta.chapter_urls)
    log.info("Scraping '%s' — %d pages  (workers=%d)", meta.title, total, workers)
    pages, failed_pages, page_has_br = _fetch_pages(session, meta.chapter_urls, workers, verbose)

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
        page_texts=pages,
        page_has_br=page_has_br,
    )


def stub_novel(url: str) -> ScrapedNovel:
    """Minimal ScrapedNovel for logging a failure where we have no parsed data."""
    meta = NovelMeta(url=url, title="", author="", status="", upload_date="")
    return ScrapedNovel(meta=meta, chapters=[], page_count=0, file_path=Path(url))
