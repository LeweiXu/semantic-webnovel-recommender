#!/usr/bin/env python3
"""Turn a TXT or EPUB novel into narrated audio using edge-tts.

EPUB input is first converted to a temporary TXT file for the TTS pass. The
temporary TXT is removed after the run unless --keep-txt is supplied.

Interrupted runs keep generated chunk audio so the same command can resume.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET


DEFAULT_VOICE = "en-US-AriaNeural"
DEFAULT_RATE = "+0%"
DEFAULT_VOLUME = "+0%"
DEFAULT_PITCH = "+0Hz"
DEFAULT_CHUNK_CHARS = 3500
SUPPORTED_INPUTS = {".epub", ".txt"}


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


def epub_to_text(epub_path: Path) -> str:
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
            print(f"Extracted EPUB chapter {index}/{len(chapter_paths)}", file=sys.stderr)
        return "\n\n".join(chapters).strip()


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def split_text(text: str, max_chars: int) -> list[str]:
    """Split text into TTS-sized chunks, preferring paragraph/sentence breaks."""
    text = normalize_text(text)
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    def push(part: str) -> None:
        nonlocal current
        part = part.strip()
        if not part:
            return
        if not current:
            current = part
            return
        if len(current) + 2 + len(part) <= max_chars:
            current += "\n\n" + part
        else:
            chunks.append(current)
            current = part

    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            push(paragraph)
            continue
        sentences = re.split(r"(?<=[.!?;:。！？；：])\s+", paragraph)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) <= max_chars:
                push(sentence)
                continue
            for piece in textwrap.wrap(
                sentence,
                width=max_chars,
                break_long_words=False,
                break_on_hyphens=False,
            ):
                push(piece)
    if current:
        chunks.append(current)
    return chunks


def chunk_hash(text: str, *, voice: str, rate: str, volume: str, pitch: str) -> str:
    payload = json.dumps(
        {
            "text": text,
            "voice": voice,
            "rate": rate,
            "volume": volume,
            "pitch": pitch,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


async def synthesize_chunk(
    text: str,
    output: Path,
    *,
    voice: str,
    rate: str,
    volume: str,
    pitch: str,
) -> None:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError(
            "edge-tts is not installed. Run: pip install edge-tts"
        ) from exc

    communicate = edge_tts.Communicate(
        text,
        voice=voice,
        rate=rate,
        volume=volume,
        pitch=pitch,
    )
    await communicate.save(str(output))


def write_ffmpeg_list(chunks: list[Path], path: Path) -> None:
    def quote(p: Path) -> str:
        return "'" + str(p).replace("'", "'\\''") + "'"

    path.write_text(
        "".join(f"file {quote(chunk.resolve())}\n" for chunk in chunks),
        encoding="utf-8",
    )


def concat_audio(chunks: list[Path], output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to combine chunks into one MP3")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        list_path = Path(f.name)
    try:
        write_ffmpeg_list(chunks, list_path)
        tmp = output.with_name(output.name + ".tmp" + output.suffix)
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(tmp),
        ]
        subprocess.run(cmd, check=True)
        os.replace(tmp, output)
    finally:
        list_path.unlink(missing_ok=True)


def input_to_text_path(path: Path, *, keep_txt: bool) -> tuple[Path, bool]:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path, False
    if suffix != ".epub":
        raise ValueError(f"Unsupported input type: {path.suffix}. Use .epub or .txt")

    txt_path = path.with_suffix(".tts.txt")
    text = epub_to_text(path)
    if not text:
        raise ValueError("EPUB extraction produced no text")
    txt_path.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote temporary TXT: {txt_path}", file=sys.stderr)
    return txt_path, not keep_txt


async def run(args: argparse.Namespace) -> int:
    if not args.input:
        print("Input .txt or .epub file is required.", file=sys.stderr)
        return 2
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 2
    if input_path.suffix.lower() not in SUPPORTED_INPUTS:
        print("Input must be .epub or .txt", file=sys.stderr)
        return 2

    output = Path(args.output).expanduser().resolve() if args.output else input_path.with_suffix(".mp3")
    chunk_dir = (
        Path(args.chunk_dir).expanduser().resolve()
        if args.chunk_dir
        else output.parent / f".{output.stem}.tts_chunks"
    )

    txt_path: Path | None = None
    remove_txt = False
    try:
        txt_path, remove_txt = input_to_text_path(input_path, keep_txt=args.keep_txt)
        text = txt_path.read_text(encoding="utf-8", errors="replace")
        chunks = split_text(text, args.chunk_chars)
        if not chunks:
            print("No readable text found.", file=sys.stderr)
            return 1

        print(
            f"TTS plan: {len(chunks)} chunk(s), voice={args.voice}, output={output}",
            file=sys.stderr,
        )
        if args.dry_run:
            return 0

        chunk_dir.mkdir(parents=True, exist_ok=True)
        chunk_paths: list[Path] = []
        for index, chunk in enumerate(chunks, 1):
            digest = chunk_hash(
                chunk,
                voice=args.voice,
                rate=args.rate,
                volume=args.volume,
                pitch=args.pitch,
            )
            chunk_path = chunk_dir / f"{index:05d}-{digest}.mp3"
            chunk_paths.append(chunk_path)
            if chunk_path.exists() and chunk_path.stat().st_size > 0 and not args.force:
                print(f"[{index}/{len(chunks)}] skip existing {chunk_path.name}", file=sys.stderr)
                continue
            print(f"[{index}/{len(chunks)}] synthesize {chunk_path.name}", file=sys.stderr)
            await synthesize_chunk(
                chunk,
                chunk_path,
                voice=args.voice,
                rate=args.rate,
                volume=args.volume,
                pitch=args.pitch,
            )

        print("Combining chunks with ffmpeg...", file=sys.stderr)
        concat_audio(chunk_paths, output)
        print(f"Wrote {output}", file=sys.stderr)

        if not args.keep_chunks:
            shutil.rmtree(chunk_dir, ignore_errors=True)
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted. Generated chunks were kept for resume.", file=sys.stderr)
        return 130
    finally:
        if remove_txt and txt_path is not None:
            txt_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a .txt or .epub novel to an MP3 audiobook using edge-tts.",
    )
    parser.add_argument("input", nargs="?", help="Input .txt or .epub file")
    parser.add_argument("-o", "--output", help="Output MP3 path; default: input basename with .mp3")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help=f"Edge voice (default: {DEFAULT_VOICE})")
    parser.add_argument("--rate", default=DEFAULT_RATE, help="Speech rate, e.g. -10%% or +15%%")
    parser.add_argument("--volume", default=DEFAULT_VOLUME, help="Volume, e.g. -10%% or +20%%")
    parser.add_argument("--pitch", default=DEFAULT_PITCH, help="Pitch, e.g. -10Hz or +20Hz")
    parser.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    parser.add_argument("--chunk-dir", help="Directory for resumable generated chunks")
    parser.add_argument("--keep-chunks", action="store_true", help="Do not remove chunk MP3s after success")
    parser.add_argument("--keep-txt", action="store_true", help="Keep temporary TXT created from EPUB")
    parser.add_argument("--force", action="store_true", help="Regenerate existing chunks")
    parser.add_argument("--dry-run", action="store_true", help="Extract/split text but do not call edge-tts")
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="Print edge-tts voices and exit (requires edge-tts installed)",
    )
    return parser


async def list_voices() -> int:
    try:
        import edge_tts
    except ImportError:
        print("edge-tts is not installed. Run: pip install edge-tts", file=sys.stderr)
        return 1
    voices = await edge_tts.list_voices()
    for voice in voices:
        print(
            f"{voice.get('ShortName', '')}\t{voice.get('Gender', '')}\t"
            f"{voice.get('Locale', '')}\t{voice.get('FriendlyName', '')}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_voices:
        return asyncio.run(list_voices())
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
