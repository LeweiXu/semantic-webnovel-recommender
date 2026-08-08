"""Tests for manual .txt upload (upload_api). Network-free, isolated to temp dirs."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1] / "reader-app" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import recsys.store as store
import upload_api


class ParseFilenameTests(unittest.TestCase):
    def test_book_dash_author_with_status(self) -> None:
        title, author, status = upload_api._parse_filename("《测试书》 - 某作者 (完结)")
        self.assertEqual((title, author, status), ("测试书", "某作者", "完结"))

    def test_underscore_split(self) -> None:
        title, author, _ = upload_api._parse_filename("公主病_鱼霜")
        self.assertEqual((title, author), ("公主病", "鱼霜"))

    def test_bracket_tags_stripped(self) -> None:
        title, _, _ = upload_api._parse_filename("【GL】难缠")
        self.assertEqual(title, "难缠")

    def test_plain_name(self) -> None:
        title, author, status = upload_api._parse_filename("My English Novel")
        self.assertEqual((title, author, status), ("My English Novel", "", ""))


class DetectTests(unittest.TestCase):
    def test_detects_chinese(self) -> None:
        text = "简介：一个故事。\n第一章 起\n正文。\n第二章 承\n更多正文。\n"
        meta = upload_api.detect("《书名》 - 作者.txt", text.encode("utf-8"))
        self.assertEqual(meta["title"], "书名")
        self.assertEqual(meta["author"], "作者")
        self.assertEqual(meta["language"], "zh")
        self.assertEqual(meta["chapter_count"], 3)  # front matter + 2 chapters
        self.assertTrue(meta["synopsis"])

    def test_detects_english(self) -> None:
        text = "Chapter 1\nHello there world.\nChapter 2\nMore prose here.\n"
        meta = upload_api.detect("A Book.txt", text.encode("utf-8"))
        self.assertEqual(meta["language"], "en")


class SaveTests(unittest.TestCase):
    def test_save_writes_utf8_file_and_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(upload_api, "LIBRARY_DIR", root), \
                 patch.object(store, "metadata_path", lambda cat: root / cat / "metadata.jsonl"):
                raw = "第一章 甲\n正文一\n第二章 乙\n正文二".encode("gb18030")  # non-UTF-8 input
                result = upload_api.save("orig.txt", raw, title="测试书", author="作者", tags=["百合", "甜文"], synopsis="简介")
                self.assertEqual(result["id"], "uploads/测试书")
                dest = root / "uploads" / "测试书.txt"
                self.assertTrue(dest.exists())
                # Stored as UTF-8 regardless of the GBK input.
                self.assertEqual(dest.read_text(encoding="utf-8").splitlines()[0], "第一章 甲")
                records = [json.loads(line) for line in (root / "uploads" / "metadata.jsonl").read_text(encoding="utf-8").splitlines()]
                rec = records[0]
                self.assertEqual(rec["category"], "uploads")
                self.assertEqual(rec["title"], "测试书")
                self.assertEqual(rec["tags"], ["百合", "甜文"])
                self.assertEqual(rec["file"], "uploads/测试书.txt")

    def test_save_avoids_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(upload_api, "LIBRARY_DIR", root), \
                 patch.object(store, "metadata_path", lambda cat: root / cat / "metadata.jsonl"):
                upload_api.save("a.txt", b"one", title="Dup")
                second = upload_api.save("a.txt", b"two", title="Dup")
                self.assertEqual(second["id"], "uploads/Dup_2")
                self.assertTrue((root / "uploads" / "Dup.txt").exists())
                self.assertTrue((root / "uploads" / "Dup_2.txt").exists())


if __name__ == "__main__":
    unittest.main()
