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

from webnovel.library import detect_language, raw_chapters, read_text_smart

import browse
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
        self.assertEqual(chapters[0].title, "Full text")

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


class ShelfTests(unittest.TestCase):
    def test_add_is_idempotent_remove_and_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_library, "LIBRARY_DIR", Path(directory)
        ):
            user_library.add("alice", "GL/a.txt", url="GL/a.txt", title="A", kind="text")
            user_library.add("alice", "GL/b.txt", url="GL/b.txt", title="B", kind="text")
            user_library.add("alice", "GL/a.txt", url="GL/a.txt", title="A2", kind="text")
            items = user_library.all_items("alice")
            self.assertEqual([it["id"] for it in items], ["GL/b.txt", "GL/a.txt"])
            self.assertEqual(items[1]["title"], "A")  # idempotent: first add wins
            self.assertTrue(user_library.remove("alice", "GL/a.txt"))
            self.assertFalse(user_library.remove("alice", "GL/a.txt"))
            self.assertEqual([it["id"] for it in user_library.all_items("alice")], ["GL/b.txt"])

    def test_users_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_library, "LIBRARY_DIR", Path(directory)
        ):
            user_library.add("alice", "x", url="x", title="X")
            self.assertEqual(user_library.all_items("bob"), [])


if __name__ == "__main__":
    unittest.main()
