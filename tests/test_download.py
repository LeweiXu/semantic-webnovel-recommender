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

    def test_failure_shows_the_scrapers_own_message(self) -> None:
        state = download_manager.DownloadState(url=URL, nid="nid1", title="x", username="tester")

        def fake_download(url, event_callback, **_kwargs):
            event_callback(f"Novel does not exist (404): {url}")
            return 1, None

        with patch.object(download_manager, "download_novel", fake_download):
            download_manager._run(state)
        self.assertEqual(state.error, f"Novel does not exist (404): {URL}")


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


class CancelTests(unittest.TestCase):
    def setUp(self) -> None:
        download_manager._states.clear()
        self.addCleanup(download_manager._states.clear)

    def test_cancel_running_download_stops_it_and_unshelves(self) -> None:
        state = download_manager.DownloadState(url=URL, nid="nid1", title="x", username="tester")
        download_manager._states[URL] = state

        def fake_download(url, cancel, **_kwargs):
            # The real scraper checks the event between pages.
            download_manager.cancel(url, "tester")
            self.assertTrue(cancel.is_set())
            raise scraper.DownloadCancelled(url)

        with patch.object(download_manager, "download_novel", fake_download), \
             patch.object(user_library, "remove") as remove:
            state.status = "queued"
            download_manager._run(state)
        self.assertEqual(state.status, "cancelled")
        self.assertNotIn(URL, download_manager._states)
        remove.assert_called_once()

    def test_cancel_queued_download_never_runs(self) -> None:
        state = download_manager.DownloadState(url=URL, nid="nid1", title="x", username="tester")
        download_manager._states[URL] = state
        with patch.object(user_library, "remove"):
            self.assertTrue(download_manager.cancel(URL, "tester"))
        self.assertNotIn(URL, download_manager._states)

    def test_page_loop_stops_when_cancelled(self) -> None:
        import threading

        cancel = threading.Event()
        fetched: list[str] = []

        def fake_fetch(_session, url, **_kwargs):
            fetched.append(url)
            cancel.set()  # cancel right after the first page
            return SimpleNamespace(text="<div id='text'>x</div>")

        urls = [f"https://www.52shuku.net/wuxia/a_{n}.html" for n in range(2, 12)]
        with patch.object(scraper, "fetch", fake_fetch), \
             patch.object(scraper, "DELAY_CHAPTER", 0), \
             patch.object(scraper, "DELAY_CHAPTER_JITTER", 0):
            with self.assertRaises(scraper.DownloadCancelled):
                scraper._fetch_pages(None, urls, workers=1, verbose=False, cancel=cancel)
        self.assertEqual(len(fetched), 1)

        # Threaded path (what the app uses): queued pages are dropped too, so
        # at most the ones already in flight get fetched.
        cancel.clear()
        fetched.clear()
        with patch.object(scraper, "fetch", fake_fetch):
            with self.assertRaises(scraper.DownloadCancelled):
                scraper._fetch_pages(None, urls * 10, workers=2, verbose=False, cancel=cancel)
        self.assertLess(len(fetched), 10)

class NotFoundTests(unittest.TestCase):
    def test_month_dir_creates_missing_category_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "52shuku" / "wuxia"  # never created
            month = scraper.upload_month_dir("2021年08月03日", base)
            self.assertTrue(month.is_dir())
            self.assertEqual(month.name, "2021-08")

    def test_local_file_error_is_not_mistaken_for_a_404(self) -> None:
        from webnovel import downloads

        def local_error(*_args, **_kwargs):
            raise FileNotFoundError("[Errno 2] No such file or directory: 'library/x'")

        with patch.object(downloads, "scrape_novel", local_error), \
             patch.object(downloads, "write_catalog") as write_catalog:
            with self.assertRaises(FileNotFoundError):
                downloads.download_novel(URL, event_callback=lambda _m: None)
            write_catalog.assert_not_called()  # no bogus "not_found" entry

    def test_real_404_is_still_recorded_as_not_found(self) -> None:
        from webnovel import downloads

        def gone(*_args, **_kwargs):
            raise scraper.PageNotFound(f"404: {URL}")

        with patch.object(downloads, "scrape_novel", gone), \
             patch.object(downloads, "load_catalog", return_value={}), \
             patch.object(downloads, "write_catalog") as write_catalog:
            code, novel = downloads.download_novel(URL, event_callback=lambda _m: None)
        self.assertEqual((code, novel), (1, None))
        saved = write_catalog.call_args.args[1]
        self.assertEqual(saved[URL].fetch_status, "not_found")


if __name__ == "__main__":
    unittest.main()
