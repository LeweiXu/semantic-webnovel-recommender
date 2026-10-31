"""Tests for manual .txt upload (upload_api). Network-free, isolated to temp dirs."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1] / "reader-app" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import recsys.store as store
import scripts.repo_paths as repo_paths
import backup
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

    def test_detects_utf16_with_bom(self) -> None:
        text = "第一章 起\n正文。\n"
        meta = upload_api.detect("书.txt", text.encode("utf-16"))
        self.assertEqual(meta["language"], "zh")
        self.assertEqual(meta["chapter_count"], 1)

    def test_detects_english(self) -> None:
        text = "Chapter 1\nHello there world.\nChapter 2\nMore prose here.\n"
        meta = upload_api.detect("A Book.txt", text.encode("utf-8"))
        self.assertEqual(meta["language"], "en")


def _library(root: Path):
    """Point the library at a temp dir (and keep backups from firing)."""
    return (
        patch.object(repo_paths, "LIBRARY_DIR", root),
        patch.object(store, "metadata_path", lambda cat: root / cat / "metadata.jsonl"),
        patch.object(backup, "schedule", lambda *a, **k: None),
    )


class SaveTests(unittest.TestCase):
    def setUp(self) -> None:
        stop = patch.object(backup, "schedule", lambda *a, **k: None)
        stop.start()
        self.addCleanup(stop.stop)

    def test_save_writes_utf8_file_and_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(repo_paths, "LIBRARY_DIR", root), \
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
            with patch.object(repo_paths, "LIBRARY_DIR", root), \
                 patch.object(store, "metadata_path", lambda cat: root / cat / "metadata.jsonl"):
                upload_api.save("a.txt", b"one", title="Dup")
                second = upload_api.save("a.txt", b"two", title="Dup")
                self.assertEqual(second["id"], "uploads/Dup_2")
                self.assertTrue((root / "uploads" / "Dup.txt").exists())
                self.assertTrue((root / "uploads" / "Dup_2.txt").exists())

    def test_save_into_subfolder_keeps_stems_unique_across_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a, b, c = _library(root)
            with a, b, c:
                upload_api.save("x.txt", b"one", title="Book", subdir="English/LN")
                second = upload_api.save("y.txt", b"two", title="Book", subdir="Other")
                self.assertTrue((root / "uploads" / "English" / "LN" / "Book.txt").exists())
                # Same title in another folder still gets its own reader slug.
                self.assertEqual(second["id"], "uploads/Book_2")
                self.assertTrue((root / "uploads" / "Other" / "Book_2.txt").exists())

    def test_subfolder_cannot_escape_uploads(self) -> None:
        self.assertEqual(upload_api.safe_subdir("../../etc"), "etc")
        self.assertEqual(upload_api.safe_subdir("a/.git/b"), "a/b")


class MoveTests(unittest.TestCase):
    def test_move_plan_moves_file_and_record_keeping_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a, b, c = _library(root)
            with a, b, c:
                saved = upload_api.save("x.txt", b"text", title="Book", subdir="Old")
                plan = root / "plan.json"
                plan.write_text(json.dumps({"Old/Book.txt": "New/Shelf"}), encoding="utf-8")
                moved = upload_api._cli_move(plan)
                self.assertEqual(moved, ["New/Shelf/Book.txt"])
                self.assertFalse((root / "uploads" / "Old").exists())  # emptied, removed
                record = store.load_category("uploads")[saved["url"]]
                self.assertEqual(record.file, "uploads/New/Shelf/Book.txt")

    def test_move_plan_can_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "uploads" / "Old").mkdir(parents=True)
            (root / "uploads" / "Old" / "123.txt").write_text("x", encoding="utf-8")
            plan = root / "plan.json"
            plan.write_text(json.dumps({"Old/123.txt": "GL/真名.txt"}), encoding="utf-8")
            a, b, c = _library(root)
            with a, b, c:
                self.assertEqual(upload_api._cli_move(plan), ["GL/真名.txt"])
            self.assertTrue((root / "uploads" / "GL" / "真名.txt").exists())


class BackupTests(unittest.TestCase):
    def _git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        ).stdout

    def test_sync_commits_and_pushes_to_remote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, repo = root / "remote.git", root / "uploads"
            self._git(root, "init", "-q", "--bare", str(remote))
            repo.mkdir()
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.name", "t")
            self._git(repo, "config", "user.email", "t@example.com")
            self._git(repo, "remote", "add", "origin", str(remote))
            (repo / "a.txt").write_text("hello", encoding="utf-8")
            self.assertTrue(backup.sync(repo))
            self.assertIn("a.txt", self._git(remote, "ls-tree", "-r", "--name-only", "HEAD"))
            # Nothing changed: still fine, no new commit.
            self.assertTrue(backup.sync(repo))
            self.assertEqual(self._git(remote, "rev-list", "--count", "HEAD").strip(), "1")

    def test_sync_is_noop_without_a_repo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(backup.sync(Path(directory)))


if __name__ == "__main__":
    unittest.main()
