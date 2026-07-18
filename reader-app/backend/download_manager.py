"""Server-side download queue.

Downloads run here on a background worker, not on the request thread, so they
keep going when the browser reloads or navigates away. A single worker processes
the queue one novel at a time (52shuku rate-limits us, so no parallel novels).
Clients watch progress by polling GET /api/downloads; there's no live connection
to drop. The only way a download fails is the scraper itself not reaching the
site.
"""
from __future__ import annotations

import queue
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from curl_cffi import requests as cffi_requests

from scraper import fetch, is_novel_landing_url, parse_landing, set_progress_callback
from webnovel.downloads import download_novel

import novels
import user_library
from ids import nid_encode

# The scraper's per-page line looks like "  [ 3/57]  fetch ...". Pull out done/total.
_PAGE_RE = re.compile(r"\[\s*(\d+)\s*/\s*(\d+)\s*\]")
# How long a finished (done/error) download stays visible to pollers.
_DONE_TTL = timedelta(minutes=15)
# Cap on novels waiting/downloading at once, shared across all users.
_MAX_ACTIVE = 10


@dataclass
class DownloadState:
    url: str
    nid: str
    title: str
    username: str
    status: str = "queued"  # queued | running | done | error
    done: int = 0
    total: int = 0
    slug: str | None = None
    error: str | None = None
    finished_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "nid": self.nid,
            "title": self.title,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "slug": self.slug,
            "error": self.error,
        }


_states: dict[str, DownloadState] = {}
_queue: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()
_worker: threading.Thread | None = None


def _predownload_title(url: str) -> str:
    """A title to show before the download finishes: from the metadata record if
    the novel is already in the store (the usual case for search/Discover), else
    a quick landing-page fetch, else the url itself."""
    record = novels.record_for_url(url)
    if record is not None:
        return record.title
    session = cffi_requests.Session()
    try:
        return parse_landing(fetch(session, url).text, url).title or url
    except Exception:  # noqa: BLE001 - a bad title shouldn't block the download
        return url
    finally:
        session.close()


def enqueue(url: str, username: str) -> dict:
    """Queue a download and put the novel on the user's shelf. Idempotent while a
    download is already queued/running; a finished/failed one re-queues (retry).

    Rejects with ValueError if the shared queue is at capacity (_MAX_ACTIVE
    novels waiting/downloading across all users) or the url isn't a novel."""
    url = (url or "").strip()
    if not is_novel_landing_url(url):
        raise ValueError(f"Not a valid 52shuku novel URL: {url}")

    with _lock:
        existing = _states.get(url)
        if existing is not None and existing.status in ("queued", "running"):
            user_library.add(username, existing.nid, url=url, title=existing.title, kind="novel", language="zh")
            return existing.to_dict()
        active = sum(1 for s in _states.values() if s.status in ("queued", "running"))
        if active >= _MAX_ACTIVE:
            raise ValueError(
                f"The download queue is full ({_MAX_ACTIVE} novels). "
                "Wait for some to finish, then try again."
            )
        # Reserve the slot now (still holding the lock) so concurrent enqueues
        # can't overshoot the cap; fill in the title outside the lock.
        state = DownloadState(url=url, nid=nid_encode(url), title="", username=username)
        _states[url] = state

    state.title = _predownload_title(url)
    user_library.add(username, state.nid, url=url, title=state.title, kind="novel", language="zh")
    _queue.put(url)
    _ensure_worker()
    return state.to_dict()


def snapshot(username: str) -> list[dict]:
    """This user's downloads (active + recently finished)."""
    now = datetime.now()
    with _lock:
        for stale in [u for u, s in _states.items() if s.finished_at and now - s.finished_at > _DONE_TTL]:
            del _states[stale]
        return [s.to_dict() for s in _states.values() if s.username == username]


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_worker_loop, name="download-worker", daemon=True)
        _worker.start()


def _worker_loop() -> None:
    while True:
        url = _queue.get()  # block until there's work; the thread lives for the process
        state = _states.get(url)
        if state is not None:
            _run(state)


def _run(state: DownloadState) -> None:
    """Download one novel, updating its state in place. Sequential by design, so
    the scraper's process-global progress callback is safe to use here."""
    state.status = "running"

    def page_progress(message: str) -> None:
        match = _PAGE_RE.search(message)
        if match:
            state.done = int(match.group(1))
            state.total = int(match.group(2))

    set_progress_callback(page_progress)
    try:
        code, novel = download_novel(state.url, event_callback=lambda _m: None)
        if code != 0 and novel is None:
            state.status = "error"
            state.error = "Download failed; the scraper couldn't reach 52shuku."
        else:
            novels.invalidate(state.url)
            record = novels.record_for_url(state.url)
            state.slug = novels.slug_for(record)
            if record is not None:
                state.total = state.done = len(novels.chapters_for(state.url, record))
            state.title = record.title if record else state.title
            state.status = "done"
    except Exception as exc:  # noqa: BLE001 - surface any failure to the poller
        state.status = "error"
        state.error = str(exc)
    finally:
        set_progress_callback(None)
        state.finished_at = datetime.now()
