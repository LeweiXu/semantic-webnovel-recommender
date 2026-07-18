"""Tests for the server-side download queue (download_manager).

Network-free: download_novel and the scraper progress are mocked, so no HTTP.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1] / "reader-app" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import download_manager
import novels
import scraper
import user_library

URL = "https://www.52shuku.net/yanqing/08_b/bkeen.html"


class DownloadManagerTests(unittest.TestCase):
    def test_run_reports_page_progress_and_finishes(self) -> None:
        record = SimpleNamespace(title="测试小说", downloaded=True)

        def fake_download(url, *, event_callback=None, **_):
            scraper._progress("  [ 1/3]  fetch 100ms")
            scraper._progress("  [ 2/3]  fetch 100ms")
            scraper._progress("  [ 3/3]  fetch 100ms")
            return 0, SimpleNamespace(failed_pages=[], skipped=False)

        state = download_manager.DownloadState(url=URL, nid="nid1", title="测试小说", username="tester")
        with patch.object(download_manager, "download_novel", fake_download), \
             patch.object(novels, "record_for_url", lambda u: record), \
             patch.object(novels, "chapters_for", lambda u, r: [1, 2, 3]), \
             patch.object(novels, "slug_for", lambda r: "yanqing/x"), \
             patch.object(novels, "invalidate", lambda u: None):
            download_manager._run(state)

        self.assertEqual(state.status, "done")
        self.assertEqual((state.done, state.total), (3, 3))
        self.assertEqual(state.slug, "yanqing/x")
        self.assertIsNotNone(state.finished_at)

    def test_run_marks_error_when_scraper_fails(self) -> None:
        state = download_manager.DownloadState(url=URL, nid="nid1", title="x", username="tester")
        with patch.object(download_manager, "download_novel", lambda u, **k: (1, None)):
            download_manager._run(state)
        self.assertEqual(state.status, "error")
        self.assertIsNotNone(state.error)

    def test_enqueue_adds_to_shelf_and_dedupes(self) -> None:
        record = SimpleNamespace(title="标题", downloaded=False)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(user_library, "LIBRARY_DIR", Path(directory)), \
             patch.object(download_manager, "_ensure_worker", lambda: None), \
             patch.object(novels, "record_for_url", lambda u: record):
            download_manager._states.clear()
            first = download_manager.enqueue(URL, "tester")
            self.assertEqual(first["status"], "queued")
            self.assertIn(first["nid"], user_library.load("tester")["items"])
            # A second enqueue while queued returns the same state, no duplicate.
            again = download_manager.enqueue(URL, "tester")
            self.assertEqual(again["nid"], first["nid"])
            self.assertEqual(download_manager.snapshot("tester"), [first])
            download_manager._states.clear()

    def test_enqueue_rejects_bad_url(self) -> None:
        with self.assertRaises(ValueError):
            download_manager.enqueue("not-a-url", "tester")

    def test_queue_cap_is_shared_across_users(self) -> None:
        record = SimpleNamespace(title="t", downloaded=False)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(user_library, "LIBRARY_DIR", Path(directory)), \
             patch.object(download_manager, "_ensure_worker", lambda: None), \
             patch.object(novels, "record_for_url", lambda u: record):
            download_manager._states.clear()
            for i in range(download_manager._MAX_ACTIVE):
                who = "alice" if i % 2 else "bob"
                download_manager.enqueue(f"https://www.52shuku.net/yanqing/{i:02d}_b/n{i}.html", who)
            with self.assertRaises(ValueError):
                download_manager.enqueue("https://www.52shuku.net/yanqing/99_b/over.html", "carol")
            download_manager._states.clear()


if __name__ == "__main__":
    unittest.main()
