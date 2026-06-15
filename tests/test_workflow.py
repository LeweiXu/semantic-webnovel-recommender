from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from recsys.catalog import CatalogRecord
from recsys.store import NovelRecord
from scraper import parse_landing
from webnovel_app.cli import build_parser, parse_categories
from webnovel_app.downloads import catalogue_urls
from webnovel_app.library import local_chapters
from webnovel_app.jobs import JobManager, JobState
from webnovel_app.targets import resolve_target


class NavigationTests(unittest.TestCase):
    def test_non_gl_navigation_is_preserved(self) -> None:
        html = """
        <h1 class="article-title">Book_Author【完结】</h1>
        <nav class="article-nav">
          <span class="article-nav-prev">
            <a href="/yanqing/old.html">Older</a>
          </span>
          <span class="article-nav-next">
            <a href="/yanqing/new.html">Newer</a>
          </span>
        </nav>
        """
        meta = parse_landing(html, "https://www.52shuku.net/yanqing/current.html")
        self.assertEqual(meta.prev_url, "https://www.52shuku.net/yanqing/old.html")
        self.assertEqual(meta.next_url, "https://www.52shuku.net/yanqing/new.html")

    def test_cross_category_navigation_is_rejected(self) -> None:
        html = """
        <h1 class="article-title">Book_Author</h1>
        <nav class="article-nav">
          <span class="article-nav-prev"><a href="/gl/old.html">Wrong</a></span>
        </nav>
        """
        meta = parse_landing(html, "https://www.52shuku.net/yanqing/current.html")
        self.assertIsNone(meta.prev_url)


class LibraryTests(unittest.TestCase):
    def test_saved_file_splits_into_chapters(self) -> None:
        text = """标题：Test
来源：https://www.52shuku.net/gl/test.html

Synopsis

════════════════════════════════════════

第1章 One

First body

════════════════════════════════════════

第2章 Two

Second body
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "novel.txt"
            path.write_text(text, encoding="utf-8")
            chapters = local_chapters(path)
        self.assertEqual([chapter.title for chapter in chapters], ["第1章 One", "第2章 Two"])
        self.assertEqual(chapters[0].body, "First body")


class CatalogueTests(unittest.TestCase):
    def test_catalogue_urls_are_oldest_first_before_queue_direction(self) -> None:
        catalog = {
            "new": CatalogRecord("new", "gl"),
            "old": CatalogRecord("old", "gl"),
            "dead": CatalogRecord("dead", "gl", "not_found"),
        }
        metadata = {
            "new": NovelRecord("new", category="gl", upload_date="2025年01月01日"),
            "old": NovelRecord("old", category="gl", upload_date="2020年01月01日"),
        }
        with patch("webnovel_app.downloads.load_catalog", return_value=catalog), patch(
            "webnovel_app.downloads.load_category", return_value=metadata
        ):
            self.assertEqual(catalogue_urls(["gl"]), ["old", "new"])


class TargetTests(unittest.TestCase):
    def test_ambiguous_partial_title_is_not_silently_selected(self) -> None:
        records = {
            "1": NovelRecord("1", title="Moon One"),
            "2": NovelRecord("2", title="Moon Two"),
        }
        with patch("webnovel_app.targets.load_all", return_value=records):
            result = resolve_target("Moon")
        self.assertIsNone(result.url)
        self.assertEqual(len(result.candidates or []), 2)


class CliTests(unittest.TestCase):
    def test_category_parser(self) -> None:
        self.assertEqual(parse_categories(["gl,yanqing", "bl"]), ["gl", "yanqing", "bl"])
        self.assertGreater(len(parse_categories("all")), 3)

    def test_primary_commands_parse(self) -> None:
        parser = build_parser()
        self.assertEqual(
            parser.parse_args(["metadata", "crawl", "--category", "gl"]).command,
            "crawl",
        )
        self.assertEqual(
            parser.parse_args(["download", "novel", "Title"]).command,
            "novel",
        )
        self.assertEqual(
            parser.parse_args(["recommend", "like", "Title"]).command,
            "like",
        )


class JobManagerTests(unittest.TestCase):
    def test_exclusive_request_pauses_and_resumes_background_job(self) -> None:
        events: list[str] = []
        manager = JobManager(events.append)
        ready = threading.Event()

        def target(controls) -> None:
            controls.register_worker()
            ready.set()
            try:
                while controls.safe_point():
                    controls.interruptible_wait(0.01)
            finally:
                controls.unregister_worker()

        self.assertTrue(manager.start("test", target))
        self.assertTrue(ready.wait(1))
        self.assertEqual(manager.run_exclusive("preview", lambda: 42), 42)
        self.assertEqual(manager.sync_state().state, JobState.RUNNING)
        self.assertTrue(manager.stop())
        self.assertTrue(manager.join(1))

    def test_interactive_fetch_blocks_new_background_job_and_is_joinable(self) -> None:
        manager = JobManager()
        entered = threading.Event()
        release = threading.Event()

        def interactive() -> None:
            entered.set()
            release.wait(1)

        thread = threading.Thread(
            target=lambda: manager.run_exclusive("novel", interactive)
        )
        thread.start()
        self.assertTrue(entered.wait(1))
        self.assertTrue(manager.busy)
        self.assertFalse(manager.start("crawl", lambda _controls: None))
        release.set()
        self.assertTrue(manager.wait_all(1))
        thread.join(1)


class TuiTests(unittest.TestCase):
    def test_app_mounts_and_switches_reader(self) -> None:
        from textual.widgets import ContentSwitcher
        from webnovel_app.library import Chapter
        from webnovel_app.tui import WebnovelApp

        async def exercise() -> None:
            app = WebnovelApp()
            async with app.run_test(size=(100, 35)) as pilot:
                await pilot.pause()
                app.dispatch_command("/help")
                app.open_reader(
                    NovelRecord("url", title="Test"),
                    [Chapter("第1章", "Body")],
                )
                self.assertEqual(
                    app.query_one("#upper", ContentSwitcher).current,
                    "reader",
                )
                app.action_leave_reader()
                self.assertEqual(
                    app.query_one("#upper", ContentSwitcher).current,
                    "interaction",
                )

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
