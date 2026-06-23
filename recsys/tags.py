"""Extract near-structured metadata embedded in 52shuku synopses.

Synopses carry author-supplied fields the site never exposed as real metadata.
Two layouts appear:

  * inside the 文案 (mostly GL, from downloaded reading pages):
        内容标签： 破镜重圆 悬疑推理 ABO 轻松 美强惨
        搜索关键字：主角：林镜疏 ┃ … ┃ 其它：ABO、刑侦、破镜重圆
        一句话简介：亲完就求婚   /   立意：不为黑暗势力打倒
  * the landing-page sidebar (all categories):
        小说简介： 《标题》作者：X【完结】　　【文案】　　<prose…>
        所属专题： 校园 / 系统 / 钓系 …          ← topic links → tags
        开始阅读 | 阅读记录   第1页第2页…          ← nav, cut here

We pull tags/one_liner/intent out and return a cleaned prose synopsis with the
header prefix, intro labels, and trailing sidebar/nav removed.
"""
from __future__ import annotations

import re

# Field labels (full/half-width colon both tolerated by the regexes below).
_LABEL = r"[：:]\s*"
_TAGS_RE = re.compile(rf"^\s*内容标签{_LABEL}(.+)$")
_KEYWORDS_RE = re.compile(rf"^\s*搜索关键字{_LABEL}(.+)$")
_ONELINER_RE = re.compile(rf"^\s*一句话简介{_LABEL}(.+)$")
_INTENT_RE = re.compile(rf"^\s*立意{_LABEL}(.+)$")
_OTHER_SEG_RE = re.compile(rf"其[它他]{_LABEL}([^┃|]+)")
# 所属专题 sidebar label; its value (if inline) and the lines after it are topics.
_TOPIC_RE = re.compile(rf"^\s*所属专题{_LABEL}(.*)$")

# Everything from here on is page navigation / chrome, not synopsis.
_STOP_RE = re.compile(r"开始阅读|阅读记录|^第\d+页第\d+页|^Tips|^上一篇|^下一篇")

# Tag/topic token separators: whitespace (incl. NBSP), Chinese/ASCII punctuation.
_TAG_SPLIT_RE = re.compile(r"[\s　\xa0、，,/／┃|；;]+")

# Bracketed genre markers inside a title, e.g. "诡秘之主[刑侦]" → 刑侦.
_BRACKET_RE = re.compile(r"[\[【［]([^\]】］]{1,8})[\]】］]")

# Lines that are pure header boilerplate / dividers, dropped from the prose.
_NOISE_EXACT = {"文案", "简介", "小说简介", "内容简介", "本书简介", "作品简介", "正文", "下载地址"}
_INTRO_NAMES = "文案|小说简介|简介|内容简介|本书简介|作品简介|正文"
# A leading intro label on an otherwise-prose line, e.g. "本书简介：心机撩…" or
# "作品简介　　永嘉郡主…" (separated by a colon OR full-width spaces).
_INTRO_LABEL_RE = re.compile(rf"^(?:{_INTRO_NAMES})(?:{_LABEL}|[\s　\xa0]+)")
# A leading bracketed intro marker, e.g. "【文案】　　裴佳媛…".
_BRACKET_INTRO_RE = re.compile(rf"^[\s　\xa0]*【(?:{_INTRO_NAMES})】[\s　\xa0]*")
# A leading "《标题》作者：X【完结】" header — stripped as a PREFIX (the 文案 often
# follows it on the same line), not by dropping the whole line.
_HEADER_PREFIX_RE = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)?《[^》]*》\s*作者[：:][^\s　\xa0【]*\s*(?:【[^】]*】)?[\s　\xa0]*"
)
_DIVIDER_RE = re.compile(r"^[\s\-—─=＝·.。·*※…_~]+$")

_MAX_TAG_LEN = 12
_NON_TAGS = {"完结", "连载", "番外", "完结+番外", "全文阅读", "最新章节"}


def _split_tags(text: str) -> list[str]:
    out: list[str] = []
    for tok in _TAG_SPLIT_RE.split(text.strip()):
        tok = tok.strip()
        if tok and len(tok) <= _MAX_TAG_LEN and tok not in _NON_TAGS:
            out.append(tok)
    return out


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def extract(synopsis_raw: str, title: str = "", author: str = "") -> dict:
    """Parse a raw synopsis block into structured fields + cleaned prose.

    Returns {"tags": [...], "one_liner": str, "intent": str, "synopsis": str}.
    """
    tags: list[str] = []
    one_liner = ""
    intent = ""
    prose_lines: list[str] = []
    in_topics = False  # inside the 所属专题 sidebar (topic links → tags)

    for raw_line in synopsis_raw.splitlines():
        line = raw_line.strip()
        if not line:
            if not in_topics:
                prose_lines.append("")
            continue

        # Page nav / chrome: nothing useful follows.
        if _STOP_RE.search(line):
            break

        m = _TOPIC_RE.match(line)
        if m:
            in_topics = True
            if m.group(1).strip():
                tags.extend(_split_tags(m.group(1)))
            continue
        if in_topics:
            tags.extend(_split_tags(line))
            continue

        m = _TAGS_RE.match(line)
        if m:
            tags.extend(_split_tags(m.group(1)))
            continue
        m = _KEYWORDS_RE.match(line)
        if m:
            for seg in _OTHER_SEG_RE.findall(m.group(1)):
                tags.extend(_split_tags(seg))
            continue
        m = _ONELINER_RE.match(line)
        if m:
            one_liner = m.group(1).strip()
            continue
        m = _INTENT_RE.match(line)
        if m:
            intent = m.group(1).strip()
            continue

        if line in _NOISE_EXACT or _DIVIDER_RE.match(line):
            continue
        # Strip a leading header / intro label, keep the prose that follows.
        line = _HEADER_PREFIX_RE.sub("", line)
        line = _INTRO_LABEL_RE.sub("", line)
        line = _BRACKET_INTRO_RE.sub("", line).strip()
        if line:
            prose_lines.append(line)

    # Bracketed genre markers from the title (e.g. 刑侦), but not status words.
    for marker in _BRACKET_RE.findall(title or ""):
        marker = marker.strip()
        if marker and marker not in _NON_TAGS:
            tags.append(marker)

    # Drop the author's name and title words that leaked in from the 所属专题 sidebar.
    noise = {author.strip()} if author else set()
    tags = [t for t in tags if t and t not in noise and t not in (title or "")]

    prose = re.sub(r"\n{3,}", "\n\n", "\n".join(prose_lines)).strip()

    return {
        "tags": _dedup_keep_order(tags),
        "one_liner": one_liner,
        "intent": intent,
        "synopsis": prose,
    }
