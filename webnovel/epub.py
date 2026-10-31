"""Plain-text extraction from EPUB files, stdlib only.

Used by scripts/tts.py (narration) and scripts/epub_to_txt.py (turning epubs into
.txt novels for the reader). Text comes out in spine order, one block per spine
item, so each XHTML file's heading leads its own section.
"""
from __future__ import annotations

import html
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET


class _TextExtractor(HTMLParser):
    """Small HTML-to-text extractor good enough for EPUB chapter XHTML."""

    BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if not self._skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if not self._skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = html.unescape("".join(self.parts))
        raw = raw.replace("\xa0", " ")
        lines = []
        for line in raw.splitlines():
            line = re.sub(r"[ \t]+", " ", line).strip()
            if line:
                lines.append(line)
        return "\n\n".join(lines)


def _xml_ns(tag: str) -> dict[str, str]:
    match = re.match(r"\{([^}]+)\}", tag)
    return {"ns": match.group(1)} if match else {}


def _epub_rootfile(zf: zipfile.ZipFile) -> str:
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
    except KeyError as exc:
        raise ValueError("EPUB is missing META-INF/container.xml") from exc
    ns = _xml_ns(container.tag)
    rootfile = container.find(".//ns:rootfile", ns) if ns else container.find(".//rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise ValueError("EPUB container does not declare a rootfile")
    return rootfile.get("full-path", "")


def _join_epub_path(base_file: str, href: str) -> str:
    base = Path(base_file).parent
    combined = base / unquote(href.split("#", 1)[0])
    parts: list[str] = []
    for part in combined.as_posix().split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def epub_to_text(epub_path: Path, *, progress: bool = True) -> str:
    """Extract spine-ordered chapter text from an EPUB file."""
    with zipfile.ZipFile(epub_path) as zf:
        rootfile = _epub_rootfile(zf)
        package = ET.fromstring(zf.read(rootfile))
        ns = _xml_ns(package.tag)
        prefix = "ns:" if ns else ""

        manifest: dict[str, str] = {}
        for item in package.findall(f".//{prefix}manifest/{prefix}item", ns):
            item_id = item.get("id")
            href = item.get("href")
            media_type = item.get("media-type", "")
            if item_id and href and media_type in {
                "application/xhtml+xml",
                "text/html",
                "application/xml",
            }:
                manifest[item_id] = _join_epub_path(rootfile, href)

        chapter_paths: list[str] = []
        for itemref in package.findall(f".//{prefix}spine/{prefix}itemref", ns):
            idref = itemref.get("idref")
            if idref in manifest:
                chapter_paths.append(manifest[idref])

        if not chapter_paths:
            raise ValueError("EPUB spine does not contain readable HTML chapters")

        chapters: list[str] = []
        for index, chapter_path in enumerate(chapter_paths, 1):
            try:
                payload = zf.read(chapter_path)
            except KeyError:
                continue
            parser = _TextExtractor()
            parser.feed(payload.decode("utf-8", errors="replace"))
            chapter = parser.text()
            if chapter:
                chapters.append(chapter)
            if progress:
                print(f"Extracted EPUB chapter {index}/{len(chapter_paths)}", file=sys.stderr)
        return "\n\n".join(chapters).strip()


def epub_metadata(epub_path: Path) -> dict[str, str]:
    """Title and author from the OPF package (empty strings when missing)."""
    with zipfile.ZipFile(epub_path) as zf:
        package = ET.fromstring(zf.read(_epub_rootfile(zf)))
    dc = "{http://purl.org/dc/elements/1.1/}"

    def first(tag: str) -> str:
        node = package.find(f".//{dc}{tag}")
        return (node.text or "").strip() if node is not None else ""

    return {"title": first("title"), "author": first("creator")}
