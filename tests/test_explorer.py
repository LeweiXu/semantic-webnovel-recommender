"""Tests for the Library file explorer, raw-file reading, and personal shelf.

Network-free like the rest of the suite: everything runs against temp dirs.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1] / "reader-app" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from webnovel.library import (
    FALLBACK_BLOCK_CHARS,
    detect_language,
    raw_chapters,
    read_text_smart,
)

import browse
import chapter_patterns
import novels
import user_library


class ReadTextTests(unittest.TestCase):
    def test_gb18030_file_decodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gbk.txt"
            path.write_bytes("第一章 测试\n这是中文。".encode("gb18030"))
            self.assertEqual(read_text_smart(path).splitlines()[0], "第一章 测试")

    def test_utf8_with_bom_decodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bom.txt"
            path.write_bytes("﻿hello".encode("utf-8"))
            self.assertEqual(read_text_smart(path), "hello")


class RawChapterTests(unittest.TestCase):
    def _chapters(self, text: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "n.txt"
            path.write_text(text, encoding="utf-8")
            return raw_chapters(path)

    def test_chinese_headings_split(self) -> None:
        chapters = self._chapters("楔子内容\n第一章 起\n正文一\n第二章 承\n正文二")
        titles = [c.title for c in chapters]
        self.assertEqual(titles, ["Front matter", "第一章 起", "第二章 承"])

    def test_english_headings_split(self) -> None:
        chapters = self._chapters("Chapter 1\nHello.\nChapter 2: Next\nMore.")
        self.assertEqual([c.title for c in chapters], ["Chapter 1", "Chapter 2: Next"])

    def test_no_headings_is_single_chapter(self) -> None:
        chapters = self._chapters("just some prose\nwith no chapter markers")
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "Part 01")

    def test_no_headings_are_split_into_bounded_virtual_chapters(self) -> None:
        chapters = self._chapters("x" * (FALLBACK_BLOCK_CHARS * 2 + 17))
        self.assertEqual(len(chapters), 3)
        self.assertTrue(all(len(chapter.body) <= FALLBACK_BLOCK_CHARS for chapter in chapters))
        self.assertEqual([chapter.title for chapter in chapters], ["Part 01", "Part 02", "Part 03"])

    def test_custom_pattern_detects_bare_numbered_chapters(self) -> None:
        text = "1重生\n正文一\n2归来\n正文二\n3终章\n正文三"
        pattern = chapter_patterns.infer("1重生")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "numbered.txt"
            path.write_text(text, encoding="utf-8")
            chapters = raw_chapters(path, pattern)
        self.assertEqual([chapter.title for chapter in chapters], ["1重生", "2归来", "3终章"])

    def test_detected_chapters_are_not_split_into_fake_continuations(self) -> None:
        body = "甲" * (FALLBACK_BLOCK_CHARS * 2 + 17)
        text = f"1重生\n{body}\n2归来\n正文二"
        pattern = chapter_patterns.infer("1重生")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "numbered.txt"
            path.write_text(text, encoding="utf-8")
            chapters = raw_chapters(path, pattern)
        self.assertEqual([chapter.title for chapter in chapters], ["1重生", "2归来"])
        self.assertEqual(chapters[0].body, body)

    def test_empty_file_has_no_chapters(self) -> None:
        self.assertEqual(self._chapters("   \n  "), [])


class LanguageTests(unittest.TestCase):
    def test_chinese_detected(self) -> None:
        self.assertEqual(detect_language("这是一部中文小说的正文内容"), "zh")

    def test_english_detected(self) -> None:
        self.assertEqual(detect_language("This is an English novel body."), "en")


class SafeJoinTests(unittest.TestCase):
    def test_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(browse, "BROWSE_DIR", Path(directory).resolve()):
                for bad in ["../etc", "a/../../etc", "/etc/passwd"]:
                    with self.assertRaises(ValueError):
                        browse.safe_join(bad)

    def test_inside_paths_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "GL").mkdir()
            with patch.object(browse, "BROWSE_DIR", root):
                self.assertEqual(browse.safe_join("GL"), root / "GL")
                self.assertEqual(browse.safe_join(""), root)


class ListDirTests(unittest.TestCase):
    def test_listing_sorts_dirs_first_and_hides_dotfiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "Zeta").mkdir()
            (root / "alpha.txt").write_text("x", encoding="utf-8")
            (root / "book.epub").write_bytes(b"x")
            (root / ".hidden").write_text("x", encoding="utf-8")
            with patch.object(browse, "BROWSE_DIR", root):
                listing = browse.list_dir("")
        names = [(e["name"], e["kind"]) for e in listing["entries"]]
        self.assertEqual(names, [("Zeta", "dir"), ("alpha.txt", "text"), ("book.epub", "doc")])
        self.assertIsNone(listing["parent"])

    def test_subdir_parent_points_at_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "GL").mkdir()
            with patch.object(browse, "BROWSE_DIR", root):
                self.assertEqual(browse.list_dir("GL")["parent"], "")


class ResolvePathTests(unittest.TestCase):
    def test_raw_txt_resolves_docs_and_missing_do_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "GL").mkdir()
            (root / "GL" / "novel.txt").write_text("第一章 甲\n内容\n第二章 乙\n内容2", encoding="utf-8")
            (root / "book.epub").write_bytes(b"x")
            with patch.object(browse, "BROWSE_DIR", root):
                resolved = novels.resolve_path("GL/novel.txt")
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.kind, "text")
                self.assertEqual(resolved.language, "zh")
                self.assertEqual(resolved.id, "GL/novel.txt")
                self.assertEqual([c.title for c in resolved.chapters], ["第一章 甲", "第二章 乙"])
                self.assertIsNone(novels.resolve_path("book.epub"))
                self.assertIsNone(novels.resolve_path("nope.txt"))
                self.assertIsNone(novels.resolve_path("../../etc/passwd"))

    def test_shared_pattern_rebuilds_fallback_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            pattern_path = root / "patterns.json"
            book = root / "bare-numbered.txt"
            book.write_text("1重生\n甲\n2归来\n乙\n3终章\n丙", encoding="utf-8")
            with patch.object(browse, "BROWSE_DIR", root), patch.object(
                chapter_patterns, "PATTERNS_PATH", pattern_path
            ):
                novels.invalidate("bare-numbered.txt")
                initial = novels.resolve_path("bare-numbered.txt")
                self.assertEqual(initial.chapter_mode, "fallback")
                chapter_patterns.set_pattern("bare-numbered.txt", chapter_patterns.infer("1重生"))
                novels.invalidate("bare-numbered.txt")
                corrected = novels.resolve_path("bare-numbered.txt")
                self.assertEqual(corrected.chapter_mode, "custom")
                self.assertEqual(
                    [chapter.title for chapter in corrected.chapters],
                    ["1重生", "2归来", "3终章"],
                )

    def test_first_chapter_is_removed_from_malformed_synopsis(self) -> None:
        from webnovel.library import Chapter

        synopsis = "A real synopsis.\n\n1重生\nChapter body accidentally included."
        self.assertEqual(
            novels.trim_synopsis_at_first_chapter(
                synopsis,
                [Chapter("1重生", "Chapter body accidentally included.")],
            ),
            "A real synopsis.",
        )


class ChapterPatternTests(unittest.TestCase):
    def test_patterns_are_shared_by_book_key_and_removable(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            chapter_patterns, "PATTERNS_PATH", Path(directory) / "patterns.json"
        ):
            pattern = r"^\s*\d+\S.*$"
            chapter_patterns.set_pattern("GL/book.txt", pattern)
            self.assertEqual(chapter_patterns.get("GL/book.txt"), pattern)
            self.assertTrue(chapter_patterns.remove("GL/book.txt"))
            self.assertIsNone(chapter_patterns.get("GL/book.txt"))

    def test_heading_examples_are_saved_and_legacy_entries_still_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            chapter_patterns, "PATTERNS_PATH", Path(directory) / "patterns.json"
        ):
            pattern = r"^\s*\d+\S.*$"
            chapter_patterns.set_pattern(
                "GL/book.txt",
                pattern,
                ["1重生", "2归来", "1重生"],
            )
            self.assertEqual(
                chapter_patterns.get_examples("GL/book.txt"),
                ["1重生", "2归来"],
            )

            chapter_patterns.PATTERNS_PATH.write_text(
                '{"version":2,"books":{"old.txt":"^Chapter"},"globals":{},'
                '"deleted_defaults":[]}',
                encoding="utf-8",
            )
            self.assertEqual(chapter_patterns.get("old.txt"), "^Chapter")
            self.assertEqual(chapter_patterns.get_examples("old.txt"), [])

    def test_unsafe_or_unanchored_patterns_are_rejected(self) -> None:
        for pattern in (r"\d+title", r"^(a+)+$", r"^(a)\1$"):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                chapter_patterns.validate(pattern)

    def test_multiple_examples_generate_one_matching_pattern(self) -> None:
        import re

        examples = ["1重生", "2 归来", "003、终章"]
        pattern = chapter_patterns.infer("\n".join(examples))
        compiled = re.compile(pattern)
        self.assertTrue(all(compiled.search(example) for example in examples))

    def test_global_patterns_can_add_edit_and_delete_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            defaults = root / "defaults.json"
            store = root / "patterns.json"
            defaults.write_text(
                '[{"id":"standard","label":"Standard","pattern":"^Chapter\\\\s+\\\\d+$"}]',
                encoding="utf-8",
            )
            with patch.object(chapter_patterns, "PATTERNS_PATH", store), patch.object(
                chapter_patterns, "DEFAULT_PATTERNS_PATH", defaults
            ):
                self.assertEqual(
                    [item["id"] for item in chapter_patterns.list_globals()],
                    ["standard"],
                )
                chapter_patterns.save_global(
                    pattern_id="standard",
                    label="Edited",
                    pattern=r"^Part\s+\d+$",
                )
                self.assertEqual(chapter_patterns.list_globals()[0]["label"], "Edited")
                added = chapter_patterns.save_global(label="Bare", pattern=r"^\d+\S+$")
                self.assertIn(added["pattern"], chapter_patterns.effective_patterns())
                self.assertTrue(chapter_patterns.remove_global("standard"))
                self.assertNotIn(
                    "standard",
                    [item["id"] for item in chapter_patterns.list_globals()],
                )


class ShelfTests(unittest.TestCase):
    def test_add_is_idempotent_and_remove_records_removal(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_library, "LIBRARY_DIR", Path(directory)
        ):
            user_library.add("alice", "GL/a.txt", url="GL/a.txt", title="A", kind="text")
            user_library.add("alice", "GL/a.txt", url="GL/a.txt", title="A2", kind="text")
            lib = user_library.load("alice")
            self.assertEqual(lib["items"]["GL/a.txt"]["title"], "A")  # first add wins
            self.assertTrue(user_library.remove("alice", "GL/a.txt"))
            self.assertFalse(user_library.remove("alice", "GL/a.txt"))
            lib = user_library.load("alice")
            self.assertNotIn("GL/a.txt", lib["items"])
            self.assertIn("GL/a.txt", lib["removed"])
            # Re-adding clears the removal.
            user_library.add("alice", "GL/a.txt", url="GL/a.txt", title="A", kind="text")
            self.assertNotIn("GL/a.txt", user_library.load("alice")["removed"])

    def test_legacy_flat_file_is_read_as_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_library, "LIBRARY_DIR", Path(directory)
        ):
            (Path(directory) / "alice.json").write_text(
                '{"GL/a.txt": {"url": "GL/a.txt", "title": "A", "kind": "text"}}',
                encoding="utf-8",
            )
            lib = user_library.load("alice")
            self.assertIn("GL/a.txt", lib["items"])
            self.assertEqual(lib["removed"], [])

    def test_users_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_library, "LIBRARY_DIR", Path(directory)
        ):
            user_library.add("alice", "x", url="x", title="X")
            self.assertEqual(user_library.load("bob"), {"items": {}, "removed": []})


if __name__ == "__main__":
    unittest.main()
