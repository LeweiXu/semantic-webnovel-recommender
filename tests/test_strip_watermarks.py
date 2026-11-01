"""Tests for scripts/strip_watermarks.py (QQ-group watermark removal)."""
from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import scripts.strip_watermarks as sw

AD = "【南锦免费外群1号】——517880761\n【南锦免费外群3号】——120320748\n"


class StripWatermarkTests(unittest.TestCase):
    def test_inline_watermarks_are_cut_at_exact_boundaries(self) -> None:
        text = AD + "\n".join([
            "然衣弍零珊尔玲VII斯玐而,",          # 120320748 inside 然而
            "什么人呜亦奇扒扒霖气⒍易?!",          # 517880761 before ?!
            "下一呜亦奇扒扒霖气⒍易刻,",           # keeps the real 一 of 下一刻
            "只不过屋I器-巴(八)〇弃刘依,",        # one filler symbol inside
        ])
        report = sw.clean(text)
        self.assertEqual(
            report["text"].split("\n"),
            ["然而，", "什么人？！", "下一刻，", "只不过，"],
        )
        self.assertEqual(report["ad_lines"], 2)
        self.assertEqual(report["insertions"], 4)

    def test_line_end_watermark_is_read_down_the_lines(self) -> None:
        body = ["“唉，等下吧。”呜", "“好的。”衣", "她答道，奇", "对此，扒",
                "随后，扒", "白发，霖", "就在此时，气", "神色，⑥", "梦曦？易",
                "‘怎么出来了？！’"]
        report = sw.clean(AD + "\n".join(body))
        lines = report["text"].split("\n")
        self.assertEqual(report["vertical"], 1)
        self.assertEqual(lines[:2], ["“唉，等下吧。”", "“好的。”"])
        self.assertEqual(lines[-2:], ["梦曦？", "‘怎么出来了？！’"])

    def test_crlf_line_endings_survive(self) -> None:
        body = ["“唉。”呜", "“好的。”衣", "她答道，奇", "对此，扒", "随后，扒",
                "白发，霖", "就在此时，气", "神色，⑥", "梦曦？易", "然衣弍零珊尔玲VII斯玐而，"]
        report = sw.clean((AD + "\n".join(body)).replace("\n", "\r\n"))
        lines = report["text"].split("\n")
        self.assertTrue(all(line.endswith("\r") for line in lines[:-1]), lines)
        self.assertEqual(lines[0], "“唉。”\r")
        self.assertEqual(lines[-1], "然而，")

    def test_ordinary_text_and_emoticons_are_left_alone(self) -> None:
        text = AD + "第一卷 寒冬大森林\n一二三四五，上山打老虎。\n“额⊙_⊙”\n他tm不想继续蹲这里了！"
        report = sw.clean(text)
        self.assertEqual(report["insertions"] + report["vertical"], 0)
        self.assertEqual(report["text"].split("\n")[1:], text.split("\n")[3:])

    def test_join_titles_merges_unless_next_line_ends_in_full_stop(self) -> None:
        lines = [
            "第四章\r", "寒冬森林\r", "渊魔的恐怖人尽皆知，\r",   # merged
            "第五章\r", "她醒了。\r",                            # first sentence: kept
            "第二十一章共浴\r",                                   # already named: kept
            "第一卷 寒冬大森林\r", "序章（上）\r",                # not a chapter header
        ]
        out, joined, skipped = sw.join_titles(lines)
        self.assertEqual(out, [
            "第四章 寒冬森林\r", "渊魔的恐怖人尽皆知，\r",
            "第五章\r", "她醒了。\r",
            "第二十一章共浴\r",
            "第一卷 寒冬大森林\r", "序章（上）\r",
        ])
        self.assertEqual((joined, len(skipped)), (1, 1))

    def test_titles_are_only_joined_when_asked(self) -> None:
        text = AD + "第四章\n寒冬森林\n正文。"
        self.assertEqual(sw.clean(text)["text"], "第四章\n寒冬森林\n正文。")
        self.assertEqual(sw.clean(text, titles=True)["text"], "第四章 寒冬森林\n正文。")

    def test_write_cleans_in_place_and_keeps_one_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book = Path(directory) / "book.txt"
            book.write_text(AD + "然衣弍零珊尔玲VII斯玐而,\n", encoding="utf-8")
            with redirect_stdout(StringIO()):
                self.assertEqual(sw.main([str(book), "--write", "--samples", "0"]), 0)
            self.assertEqual(book.read_text(encoding="utf-8"), "然而，\n")
            backup = book.with_name(".book.txt.bak")
            self.assertIn("衣弍零", backup.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
