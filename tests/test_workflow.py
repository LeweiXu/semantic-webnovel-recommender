from __future__ import annotations

import tempfile
import unittest
import zipfile
import sys
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1] / "reader-app" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from recsys.catalog import CatalogRecord
from recsys.store import NovelRecord
from scraper import parse_landing
from webnovel.downloads import catalogue_urls
from webnovel.library import (
    MAX_CHAPTER_CHARS,
    Chapter,
    chapter_number,
    chapters_from_text,
    local_chapters,
    local_synopsis,
    read_text_smart,
)
from webnovel.targets import resolve_target

import download
import read
import report
import scrape_metadata
import scripts.tts as tts


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

    def test_saved_file_custom_pattern_removes_storage_dividers(self) -> None:
        text = """标题：Test
来源：https://example.test/book

Synopsis

════════════════════════════════════════

1重生

First body

════════════════════════════════════════

2归来

Second body
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "novel.txt"
            path.write_text(text, encoding="utf-8")
            chapters = local_chapters(path, r"^\s*\d+\S.*$")
        self.assertEqual([chapter.title for chapter in chapters], ["1重生", "2归来"])
        self.assertNotIn("═", chapters[0].body)

    def test_saved_file_without_detected_titles_uses_virtual_parts(self) -> None:
        text = """标题：Test
来源：https://example.test/book

Synopsis

════════════════════════════════════════

1重生
First body

════════════════════════════════════════

2归来
Second body
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "novel.txt"
            path.write_text(text, encoding="utf-8")
            chapters = local_chapters(path)
        self.assertTrue(chapters)
        self.assertTrue(all(chapter.title.startswith("Part ") for chapter in chapters))

    def assertBoundedParts(self, chapters: list[Chapter], source: str) -> None:
        """Parts round up to the nearest line, overshooting by at most one line."""
        lines = source.split("\n")
        longest_line = max(len(line) for line in lines)
        whole_lines = {line for line in lines if line.strip()}
        for chapter in chapters:
            self.assertLessEqual(len(chapter.body), MAX_CHAPTER_CHARS + longest_line)
            # A line cut in half wouldn't match any line of the source.
            for line in chapter.body.split("\n"):
                if line.strip():
                    self.assertIn(line, whole_lines)

    def _write(self, directory: str, body: str) -> Path:
        path = Path(directory) / "novel.txt"
        path.write_text(
            f"标题：Test\n作者：A\n来源：https://example.test/book\n\n{body}",
            encoding="utf-8",
        )
        return path

    def test_chapter_number_reads_arabic_and_chinese_ordinals(self) -> None:
        cases = {
            "第1章 One": 1,
            "第五章 Five": 5,
            "第十五章": 15,
            "第二十三章": 23,
            "第一百零三回": 103,
            "Chapter 7": 7,
            "12归来": 12,
            "楔子": None,
            "番外": None,
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(chapter_number(title), expected)

    def test_numbering_gap_splits_the_fused_chapter(self) -> None:
        # 第三章 is followed by 第十章, so chapters four-nine were never detected
        # and their text is sitting inside 第三章's body.
        buried = "\n\n".join(f"未标记段落{n}\n\n{'字' * 900}" for n in range(1, 16))
        normal = "文" * 1200  # distinct from the buried text's 字 marker
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(
                directory,
                f"第一章 One\n\n{normal}\n\n第二章 Two\n\n{normal}\n\n"
                f"第三章 Three\n\n{buried}\n\n第十章 Ten\n\n{normal}\n",
            )
            chapters = local_chapters(path)

        titles = [chapter.title for chapter in chapters]
        self.assertEqual(titles[0], "第一章 One")
        self.assertIn("第三章 Three (2)", titles)
        self.assertIn("第十章 Ten", titles)
        # Normal chapters either side of the fused one are left alone.
        self.assertEqual(titles.count("第一章 One"), 1)
        self.assertNotIn("第二章 Two (2)", titles)
        # Parts are small enough for the reader to render with pinyin/ruby, and
        # each ends on a line boundary rather than mid-line.
        fused_parts = [c for c in chapters if c.title.startswith("第三章 Three")]
        self.assertBoundedParts(fused_parts, buried)
        # The chapter that isn't fused is left exactly as it was.
        self.assertEqual(titles.count("第十章 Ten"), 1)
        self.assertNotIn("第十章 Ten (2)", titles)
        # No text is lost or duplicated by the re-split.
        rejoined = "".join(chapter.body for chapter in chapters)
        self.assertEqual(rejoined.count("字" * 900), 15)

    def test_front_matter_before_first_numbered_chapter_is_split(self) -> None:
        # The reported case: chapters 1-15 undetected, so the first heading found
        # is 第16章 and everything above it is one fused block.
        buried = "\n\n".join(f"未标记段落{n}\n\n{'字' * 900}" for n in range(1, 16))
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(
                directory,
                f"{buried}\n\n第16章 Sixteen\n\n短正文\n\n第17章 Seventeen\n\n短正文\n",
            )
            source = read_text_smart(path)
            chapters = chapters_from_text(source)

        titles = [chapter.title for chapter in chapters]
        self.assertIn("第16章 Sixteen", titles)
        self.assertIn("第17章 Seventeen", titles)
        # The front matter above 第16章 held chapters 1-15, so it was split.
        self.assertGreater(sum(1 for t in titles if t.startswith("Front matter")), 1, titles)
        front = [c for c in chapters if c.title.startswith("Front matter")]
        self.assertBoundedParts(front, source)
        # The two real chapters are untouched.
        self.assertEqual(titles.count("第16章 Sixteen"), 1)
        self.assertEqual(titles.count("第17章 Seventeen"), 1)

    def test_long_chapter_without_a_numbering_gap_is_left_whole(self) -> None:
        # The point of using numbering rather than length: consecutive ordinals
        # mean nothing is missing, so a genuinely long chapter stays one chapter.
        long_body = "字" * (MAX_CHAPTER_CHARS * 6)
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, f"第1章 One\n\n{long_body}\n\n第2章 Two\n\n短正文\n")
            chapters = local_chapters(path)

        titles = [chapter.title for chapter in chapters]
        self.assertEqual(titles, ["第1章 One", "第2章 Two"])
        self.assertEqual(len(chapters[0].body), len(long_body))

    def test_saved_file_extracts_synopsis_after_preamble(self) -> None:
        text = """标题：Test
作者：A
来源：https://www.52shuku.net/gl/test.html

[ＧＬ百合] 《Test》作者：A【完结】

简介：Synopsis body.

════════════════════════════════════════

第1章 One

First body
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "novel.txt"
            path.write_text(text, encoding="utf-8")
            synopsis = local_synopsis(path)
        self.assertIsNotNone(synopsis)
        self.assertEqual(synopsis.title, "Synopsis")
        self.assertIn("简介：Synopsis body.", synopsis.body)
        self.assertNotIn("标题：Test", synopsis.body)

    def test_file_without_divider_is_not_one_giant_synopsis(self) -> None:
        text = """标题：Test
来源：https://example.test/book

1重生
正文
2归来
正文
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "novel.txt"
            path.write_text(text, encoding="utf-8")
            synopsis = local_synopsis(path)
        self.assertIsNone(synopsis)


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


class MetadataCrawlerTests(unittest.TestCase):
    def test_total_metadata_counts_all_selected_category_stores(self) -> None:
        from recsys.crawl import MetaCrawler

        stores = {
            "gl": {
                "g1": NovelRecord("g1", category="gl"),
                "g2": NovelRecord("g2", category="gl"),
            },
            "yanqing": {
                "y1": NovelRecord("y1", category="yanqing"),
            },
        }
        with patch("recsys.crawl.load_category", side_effect=lambda cat: stores[cat]), patch(
            "recsys.crawl.load_catalog", return_value={}
        ):
            crawler = MetaCrawler(["gl", "yanqing"])
        self.assertEqual(crawler.total_metadata, 3)


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

    def test_download_subcommands_parse(self) -> None:
        parser = download.build_parser()
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

    def test_read_no_synopsis_flag(self) -> None:
        parser = read.build_parser()
        self.assertTrue(parser.parse_args(["Title", "--no-synopsis"]).no_synopsis)


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

    def test_copy_payload_prepends_synopsis_only_from_chapter_one(self) -> None:
        chapters = [
            Chapter("第1章 One", "First"),
            Chapter("第2章 Two", "Second"),
        ]
        synopsis = Chapter("Synopsis", "Intro")
        self.assertEqual(
            read._payload_for_span(
                chapters, 0, 1, synopsis=synopsis, include_synopsis=True
            ),
            "Synopsis\n\nIntro\n\n第1章 One\n\nFirst",
        )
        self.assertEqual(
            read._payload_for_span(
                chapters, 1, 2, synopsis=synopsis, include_synopsis=True
            ),
            "第2章 Two\n\nSecond",
        )
        self.assertEqual(
            read._payload_for_span(
                chapters, 0, 1, synopsis=synopsis, include_synopsis=False
            ),
            "第1章 One\n\nFirst",
        )


class ReaderAuthTests(unittest.TestCase):
    def test_password_hash_and_jwt_round_trip(self) -> None:
        import auth

        password_hash = auth.hash_password("correct horse battery staple")
        self.assertTrue(auth.verify_password("correct horse battery staple", password_hash))
        self.assertFalse(auth.verify_password("wrong password", password_hash))
        with patch.dict("os.environ", {"NOVEL_JWT_SECRET": "test-secret"}):
            token = auth.create_token("alice")
            self.assertEqual(auth.decode_token(token), "alice")


class AdminJobTests(unittest.TestCase):
    def test_argv_builder_accepts_only_known_scripts(self) -> None:
        import admin_jobs

        argv = admin_jobs.build_argv(
            "download categories gl --limit 50", python="/venv/bin/python"
        )
        self.assertEqual(argv[0], "/venv/bin/python")
        self.assertEqual(Path(argv[1]).name, "download.py")
        self.assertEqual(argv[2:], ["categories", "gl", "--limit", "50"])

        for command in ("rm -rf /", "download categories gl; rm -rf /", "download $(id)"):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    admin_jobs.build_argv(command)


class UserProgressTests(unittest.TestCase):
    def test_users_are_isolated_and_positions_never_rewind(self) -> None:
        import user_progress

        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_progress, "PROGRESS_DIR", Path(directory)
        ):
            user_progress.set_position("alice", "novel", 5, title="N", total=10)
            user_progress.set_position("alice", "novel", 2, title="N", total=10)
            user_progress.set_position("bob", "novel", 1, title="N", total=10)

            self.assertEqual(user_progress.get_position("alice", "novel"), 5)
            self.assertEqual(user_progress.get_position("bob", "novel"), 1)
            self.assertEqual(user_progress.all_progress("alice")["novel"]["position"], 5)

    def test_character_anchor_is_monotonic_and_can_be_force_reset(self) -> None:
        import user_progress

        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_progress, "PROGRESS_DIR", Path(directory)
        ):
            user_progress.set_position("alice", "novel", 3, 18)
            user_progress.set_position("alice", "novel", 3, 7)
            self.assertEqual(user_progress.get_entry("alice", "novel")["line"], 18)
            self.assertEqual(user_progress.get_entry("alice", "novel")["anchor_version"], 2)
            user_progress.set_position("alice", "novel", 3, 7, force=True)
            self.assertEqual(user_progress.get_entry("alice", "novel")["line"], 7)

    def test_character_anchor_replaces_legacy_rendered_line(self) -> None:
        import json
        import user_progress

        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_progress, "PROGRESS_DIR", Path(directory)
        ):
            path = Path(directory) / "alice.json"
            path.write_text(json.dumps({"novel": {"position": 3, "line": 80}}))
            # The stable character offset may be numerically lower than the old
            # layout-dependent line; its newer version must still win.
            user_progress.set_position("alice", "novel", 3, 40, anchor_version=2)
            entry = user_progress.get_entry("alice", "novel")
            self.assertEqual(entry["line"], 40)
            self.assertEqual(entry["anchor_version"], 2)

    def test_unstarted_chapter_has_no_line_bookmark(self) -> None:
        import user_progress

        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_progress, "PROGRESS_DIR", Path(directory)
        ):
            user_progress.set_position("alice", "novel", 0, None)
            self.assertNotIn("line", user_progress.get_entry("alice", "novel"))
            user_progress.set_position("alice", "novel", 0, 0)
            self.assertEqual(user_progress.get_entry("alice", "novel")["line"], 0)


class UserSettingsTests(unittest.TestCase):
    def test_per_profile_settings_and_unknown_keys_are_discarded(self) -> None:
        import user_settings

        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_settings, "SETTINGS_DIR", Path(directory)
        ):
            saved = user_settings.put(
                "alice",
                {
                    "desktop": {"contrast": 125, "unknown": "value"},
                    "mobile": {"fontSize": 18},
                },
            )
            self.assertEqual(
                saved, {"desktop": {"contrast": 125}, "mobile": {"fontSize": 18}}
            )
            self.assertEqual(
                user_settings.get("alice"),
                {"desktop": {"contrast": 125}, "mobile": {"fontSize": 18}},
            )

    def test_partial_put_leaves_the_other_profile_untouched(self) -> None:
        import user_settings

        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_settings, "SETTINGS_DIR", Path(directory)
        ):
            user_settings.put("bob", {"desktop": {"theme": "night"}})
            user_settings.put("bob", {"mobile": {"theme": "paper"}})
            self.assertEqual(
                user_settings.get("bob"),
                {"desktop": {"theme": "night"}, "mobile": {"theme": "paper"}},
            )

    def test_legacy_flat_file_is_read_as_the_desktop_profile(self) -> None:
        import user_settings

        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_settings, "SETTINGS_DIR", Path(directory)
        ):
            (Path(directory) / "carol.json").write_text(
                '{"contrast": 110, "unknown": "x"}', encoding="utf-8"
            )
            self.assertEqual(
                user_settings.get("carol"),
                {"desktop": {"contrast": 110}, "mobile": {}},
            )


class TtsTests(unittest.TestCase):
    def _write_epub(self, path: Path) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr(
                "META-INF/container.xml",
                """<?xml version="1.0"?>
                <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
                  <rootfiles>
                    <rootfile full-path="OPS/content.opf"
                      media-type="application/oebps-package+xml"/>
                  </rootfiles>
                </container>
                """,
            )
            zf.writestr(
                "OPS/content.opf",
                """<?xml version="1.0"?>
                <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
                  <manifest>
                    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
                    <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
                  </manifest>
                  <spine>
                    <itemref idref="c1"/>
                    <itemref idref="c2"/>
                  </spine>
                </package>
                """,
            )
            zf.writestr(
                "OPS/chapter1.xhtml",
                "<html><body><h1>Chapter One</h1><p>Hello world.</p></body></html>",
            )
            zf.writestr(
                "OPS/chapter2.xhtml",
                "<html><body><h1>Chapter Two</h1><p>Goodbye.</p></body></html>",
            )

    def test_epub_to_text_uses_spine_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.epub"
            self._write_epub(path)
            with redirect_stderr(StringIO()):
                text = tts.epub_to_text(path)
        self.assertLess(text.index("Chapter One"), text.index("Chapter Two"))
        self.assertIn("Hello world.", text)

    def test_epub_dry_run_removes_temporary_txt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.epub"
            self._write_epub(path)
            err = StringIO()
            with redirect_stderr(err):
                code = tts.main([str(path), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertFalse(path.with_suffix(".tts.txt").exists())
            self.assertIn("TTS plan:", err.getvalue())

    def test_chunking_respects_limit(self) -> None:
        chunks = tts.split_text("One. Two. Three.\n\nFour.", 12)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 12 for chunk in chunks))

    def test_concat_temp_output_keeps_audio_extension(self) -> None:
        calls = []

        def fake_run(cmd, check):
            calls.append(cmd)
            Path(cmd[-1]).write_bytes(b"audio")

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            chunk = base / "chunk.mp3"
            chunk.write_bytes(b"audio")
            output = base / "book.mp3"
            with patch("tts.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
                "tts.subprocess.run", side_effect=fake_run
            ):
                tts.concat_audio([chunk], output)
            self.assertTrue(output.exists())
            self.assertTrue(calls[0][-1].endswith(".tmp.mp3"))


if __name__ == "__main__":
    unittest.main()
