from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recsys.catalog import CatalogRecord
from recsys.store import NovelRecord
from scraper import parse_landing
from webnovel.downloads import catalogue_urls
from webnovel.library import local_chapters
from webnovel.targets import resolve_target

import download_catalogue
import read
import report
import scrape_metadata


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
        with patch("webnovel.downloads.load_catalog", return_value=catalog), patch(
            "webnovel.downloads.load_category", return_value=metadata
        ):
            self.assertEqual(catalogue_urls(["gl"]), ["old", "new"])


class TargetTests(unittest.TestCase):
    def test_ambiguous_partial_title_is_not_silently_selected(self) -> None:
        records = {
            "1": NovelRecord("1", title="Moon One"),
            "2": NovelRecord("2", title="Moon Two"),
        }
        with patch("webnovel.targets.load_all", return_value=records):
            result = resolve_target("Moon")
        self.assertIsNone(result.url)
        self.assertEqual(len(result.candidates or []), 2)


class ScriptParserTests(unittest.TestCase):
    def test_scrape_metadata_categories_default_and_explicit(self) -> None:
        self.assertEqual(
            scrape_metadata.parse_categories(["gl,yanqing", "bl"]),
            ["gl", "yanqing", "bl"],
        )
        self.assertGreater(len(scrape_metadata.parse_categories(None)), 3)

    def test_download_catalogue_subcommands_parse(self) -> None:
        parser = download_catalogue.build_parser()
        self.assertEqual(parser.parse_args(["categories", "gl"]).command, "categories")
        self.assertEqual(parser.parse_args(["novel", "Title"]).command, "novel")
        self.assertEqual(parser.parse_args(["repair"]).command, "repair")

    def test_report_subcommands_parse(self) -> None:
        parser = report.build_parser()
        self.assertEqual(parser.parse_args(["catalogue"]).command, "catalogue")
        self.assertEqual(parser.parse_args(["size"]).command, "size")
        self.assertEqual(parser.parse_args(["chains", "--category", "gl"]).command, "chains")

    def test_read_copy_flag_defaults_to_one(self) -> None:
        parser = read.build_parser()
        self.assertEqual(parser.parse_args(["Title", "--copy"]).copy, 1)
        self.assertEqual(parser.parse_args(["Title", "--copy", "3"]).copy, 3)
        self.assertIsNone(parser.parse_args(["Title"]).copy)


class ReadingProgressTests(unittest.TestCase):
    def test_bookmark_round_trips_and_clears(self) -> None:
        from webnovel import progress

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reading_progress.json"
            with patch.object(progress, "PROGRESS_PATH", path):
                self.assertEqual(progress.get_position("u"), 0)
                progress.set_position("u", 5, title="T", total=120)
                self.assertEqual(progress.get_position("u"), 5)
                self.assertIn("u", progress.all_progress())
                self.assertTrue(progress.clear("u"))
                self.assertEqual(progress.get_position("u"), 0)


if __name__ == "__main__":
    unittest.main()
