"""Server-sent-events bridge over the blocking download_novel().

download_novel() runs on a worker thread and emits progress strings via its
event_callback. We funnel those into a queue and drain it as an SSE stream so the
browser sees live progress, then a terminal ``done`` or ``error`` event.
"""
from __future__ import annotations

import json
import queue
import threading

from scraper import is_novel_landing_url
from webnovel.downloads import download_novel

import novels
from ids import nid_encode

_SENTINEL = object()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def download_events(url: str):
    """Yield SSE-formatted strings for a single download."""
    url = (url or "").strip()
    if not is_novel_landing_url(url):
        yield _sse("error", {"message": f"Not a valid 52shuku novel URL: {url}"})
        return

    events: "queue.Queue" = queue.Queue()

    def emit(message: str) -> None:
        events.put(("progress", {"message": str(message)}))

    result: dict = {}

    def worker() -> None:
        try:
            code, novel = download_novel(url, event_callback=emit)
            result["code"] = code
            result["novel"] = novel
        except Exception as exc:  # noqa: BLE001 - surface any failure to the client
            result["error"] = str(exc)
        finally:
            events.put((_SENTINEL, None))

    thread = threading.Thread(target=worker, name="reader-download", daemon=True)
    thread.start()

    yield _sse("progress", {"message": f"Starting download: {url}"})
    while True:
        kind, payload = events.get()
        if kind is _SENTINEL:
            break
        yield _sse(kind, payload)

    thread.join()
    if "error" in result:
        yield _sse("error", {"message": result["error"]})
        return
    if result.get("code", 1) != 0 and result.get("novel") is None:
        yield _sse("error", {"message": "Download failed; see backend logs."})
        return

    novels.invalidate(url)
    record = novels.record_for_url(url)
    title = record.title if record else url
    total = len(novels.chapters_for(url, record)) if record else 0
    yield _sse("done", {"nid": nid_encode(url), "url": url, "title": title, "chapters": total})
