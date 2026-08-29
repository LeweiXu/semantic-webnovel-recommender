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


class JoinLinesEndpointTests(unittest.TestCase):
    """The admin-only .txt repair route (POST /api/novel/{nid}/join-lines)."""

    def _run(self, text: str):
        from fastapi.testclient import TestClient
        import app as backend_app
        from auth import require_admin

        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        target = root / "novel.txt"
        target.write_text(text, encoding="utf-8")
        with patch.object(browse, "BROWSE_DIR", root):
            novels._chapter_cache.clear()
            # Stand in for the admin dependency rather than creating an account.
            backend_app.app.dependency_overrides[require_admin] = lambda: "lingwei"
            try:
                client = TestClient(backend_app.app)
                response = client.post("/api/novel/novel.txt/join-lines")
            finally:
                backend_app.app.dependency_overrides.clear()
                novels._chapter_cache.clear()
        return response, target, directory

    def test_join_rewrites_the_file_and_keeps_one_backup(self) -> None:
        text = "第一章 开端\n\n这是第一句\n被拆成了两行。\n\n第二章 继续\n\n正文。\n"
        response, target, directory = self._run(text)
        with directory:
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["joins"], 1)
            self.assertEqual(body["chapters"], 2)

            written = target.read_text(encoding="utf-8")
            self.assertIn("这是第一句被拆成了两行。", written)
            # The untouched original is kept as a dotfile, which the file
            # explorer hides, so it never shows up as a stray entry.
            backup = target.with_name(f".{target.name}.bak")
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(encoding="utf-8"), text)
            self.assertTrue(body["backup"].startswith("."))
            # No temp file left behind by the atomic swap.
            self.assertFalse(target.with_name(f".{target.name}.tmp").exists())

    def test_join_is_a_no_op_when_nothing_is_split(self) -> None:
        text = "第一章 开端\n\n完整的一句话。\n\n第二章 继续\n\n正文。\n"
        response, target, directory = self._run(text)
        with directory:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["joins"], 0)
            self.assertIsNone(response.json()["backup"])
            # Nothing was written, so no backup and the file is byte-identical.
            self.assertEqual(target.read_text(encoding="utf-8"), text)
            self.assertFalse(target.with_name(f".{target.name}.bak").exists())

    def test_join_requires_admin(self) -> None:
        from fastapi.testclient import TestClient
        import app as backend_app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "novel.txt").write_text("第一章 开端\n\n正文。\n", encoding="utf-8")
            with patch.object(browse, "BROWSE_DIR", root):
                novels._chapter_cache.clear()
                client = TestClient(backend_app.app)
                # No credentials at all: the route must not run.
                response = client.post("/api/novel/novel.txt/join-lines")
                novels._chapter_cache.clear()
        self.assertIn(response.status_code, (401, 403))


class UserBookmarkTests(unittest.TestCase):
    """The per-user store behind the reader's Bookmarks view."""

    def test_bookmarks_are_per_novel_newest_first_and_deletable(self) -> None:
        import user_bookmarks

        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_bookmarks, "BOOKMARK_DIR", Path(directory)
        ):
            user_bookmarks.add("alice", "novel", chapter=1, line=10, excerpt="one")
            rows = user_bookmarks.add("alice", "novel", chapter=4, line=None, excerpt="two")
            self.assertEqual([row["chapter"] for row in rows], [4, 1])
            self.assertIsNone(rows[0]["line"])

            # A second novel keeps its own list.
            user_bookmarks.add("alice", "other", chapter=0, line=0)
            self.assertEqual(len(user_bookmarks.list_for("alice", "novel")), 2)
            self.assertEqual(len(user_bookmarks.list_for("alice", "other")), 1)

            left = user_bookmarks.remove("alice", "novel", rows[0]["id"])
            self.assertEqual([row["chapter"] for row in left], [1])
            # Deleting something already gone is a no-op, not an error.
            self.assertEqual(len(user_bookmarks.remove("alice", "novel", "nope")), 1)

    def test_the_same_position_is_never_saved_twice(self) -> None:
        import user_bookmarks

        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_bookmarks, "BOOKMARK_DIR", Path(directory)
        ):
            user_bookmarks.add("alice", "novel", chapter=2, line=44)
            rows = user_bookmarks.add("alice", "novel", chapter=2, line=44)
            self.assertEqual(len(rows), 1)
            # The top of a chapter and a position inside it are different places.
            rows = user_bookmarks.add("alice", "novel", chapter=2, line=None)
            self.assertEqual(len(rows), 2)

    def test_excerpt_is_collapsed_and_capped(self) -> None:
        import user_bookmarks

        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_bookmarks, "BOOKMARK_DIR", Path(directory)
        ):
            rows = user_bookmarks.add(
                "alice", "novel", chapter=0, excerpt=" a\n b " + "x" * 400
            )
            self.assertEqual(len(rows[0]["excerpt"]), user_bookmarks.MAX_EXCERPT)
            self.assertTrue(rows[0]["excerpt"].startswith("a b"))

    def test_users_are_isolated(self) -> None:
        import user_bookmarks

        with tempfile.TemporaryDirectory() as directory, patch.object(
            user_bookmarks, "BOOKMARK_DIR", Path(directory)
        ):
            user_bookmarks.add("alice", "novel", chapter=1)
            self.assertEqual(user_bookmarks.list_for("bob", "novel"), [])


class ReaderApiTests(unittest.TestCase):
    """Word counts and bookmarks over the HTTP API, on a raw browsed .txt."""

    TEXT = "第一章 开端\n\n上来就两句话。\n\n第二章 继续\n\n下一章短一点。\n"

    def _client(self, root: Path, bookmark_dir: Path):
        from fastapi.testclient import TestClient
        import app as backend_app
        import user_bookmarks
        from auth import current_user

        (root / "novel.txt").write_text(self.TEXT, encoding="utf-8")
        backend_app.app.dependency_overrides[current_user] = lambda: "alice"
        patches = [
            patch.object(browse, "BROWSE_DIR", root),
            patch.object(user_bookmarks, "BOOKMARK_DIR", bookmark_dir),
        ]
        for item in patches:
            item.start()
        novels._chapter_cache.clear()
        return TestClient(backend_app.app), patches

    def _teardown(self, patches) -> None:
        import app as backend_app

        for item in patches:
            item.stop()
        backend_app.app.dependency_overrides.clear()
        novels._chapter_cache.clear()

    def test_word_counts_are_reported_per_chapter_and_for_the_book(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as marks:
            client, patches = self._client(Path(directory), Path(marks))
            try:
                detail = client.get("/api/novel/novel.txt").json()
                chapter = client.get("/api/novel/novel.txt/chapter/0?annotate=0").json()
            finally:
                self._teardown(patches)

        words = [c["words"] for c in detail["chapters"]]
        self.assertEqual(words, [len("上来就两句话。"), len("下一章短一点。")])
        self.assertEqual(detail["total_words"], sum(words))
        # The chapter payload carries the same count the contents list shows.
        self.assertEqual(chapter["words"], words[0])

    def test_bookmarks_round_trip_through_the_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as marks:
            client, patches = self._client(Path(directory), Path(marks))
            try:
                self.assertEqual(client.get("/api/novel/novel.txt/bookmarks").json(), [])
                rows = client.post(
                    "/api/novel/novel.txt/bookmarks",
                    json={"chapter": 1, "line": 3, "chapter_title": "wrong", "excerpt": "下一章"},
                ).json()
                self.assertEqual(len(rows), 1)
                # The heading comes from the book, not from what the client sent.
                self.assertEqual(rows[0]["chapter_title"], "第二章 继续")
                self.assertEqual(rows[0]["line"], 3)

                listed = client.get("/api/novel/novel.txt/bookmarks").json()
                self.assertEqual(listed, rows)

                left = client.delete(
                    f"/api/novel/novel.txt/bookmarks/{rows[0]['id']}"
                ).json()
                self.assertEqual(left, [])
            finally:
                self._teardown(patches)

    def test_a_bookmark_past_the_last_chapter_is_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as marks:
            client, patches = self._client(Path(directory), Path(marks))
            try:
                rows = client.post(
                    "/api/novel/novel.txt/bookmarks", json={"chapter": 99, "line": None}
                ).json()
            finally:
                self._teardown(patches)
        self.assertEqual(rows[0]["chapter"], 1)

    def test_bookmarks_require_a_login(self) -> None:
        from fastapi.testclient import TestClient
        import app as backend_app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "novel.txt").write_text(self.TEXT, encoding="utf-8")
            with patch.object(browse, "BROWSE_DIR", root):
                novels._chapter_cache.clear()
                client = TestClient(backend_app.app)
                response = client.get("/api/novel/novel.txt/bookmarks")
                novels._chapter_cache.clear()
        self.assertEqual(response.status_code, 401)
