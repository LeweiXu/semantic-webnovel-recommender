"""Pydantic response models for the reader API."""
from __future__ import annotations

from pydantic import BaseModel


class RegisterIn(BaseModel):
    username: str
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str
    created: str = ""


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ReadingItem(BaseModel):
    url: str
    nid: str
    slug: str | None = None
    title: str
    author: str = ""
    category: str = ""
    position: int = 0
    line: int | None = None
    total: int | None = None
    updated: str = ""
    tags: list[str] = []
    synopsis: str = ""


class ShelfItem(BaseModel):
    id: str  # route id: metadata slug or raw browse path
    title: str
    author: str = ""
    category: str = ""
    kind: str = "novel"  # "novel" | "text" | "doc"
    language: str = "zh"
    downloaded: bool = True  # False while a novel is still downloading
    url: str = ""  # progress/download key (52shuku url or browse path)
    position: int = 0
    total: int | None = None
    updated: str = ""  # last read; "" if never opened
    added: str = ""
    tags: list[str] = []
    synopsis: str = ""


class SearchItem(BaseModel):
    url: str
    nid: str
    slug: str | None = None
    title: str
    author: str = ""
    category: str = ""
    downloaded: bool = False
    chapter_count: int | None = None


class BrowseEntry(BaseModel):
    name: str
    path: str  # browse-root-relative posix path
    kind: str  # "dir" | "text" | "doc" | "other"
    size: int | None = None


class BrowseListing(BaseModel):
    path: str  # "" at the root
    parent: str | None = None  # None at the root; "" means the root is the parent
    entries: list[BrowseEntry]


class ChapterStub(BaseModel):
    index: int
    title: str


class Token(BaseModel):
    t: str
    py: str | None = None


class NovelDetail(BaseModel):
    url: str
    nid: str
    slug: str
    title: str
    author: str = ""
    category: str = ""
    tags: list[str] = []
    synopsis: str = ""
    synopsis_tokens: list[Token] = []
    downloaded: bool = True
    total: int
    position: int = 0
    line: int | None = None
    anchor_version: int = 2
    chapters: list[ChapterStub]
    kind: str = "novel"  # "novel" = metadata-backed, "text" = raw browsed file
    language: str = "zh"  # "zh" | "en" — drives pinyin/ruby on the client
    chapter_mode: str = "detected"  # "detected" | "fallback" | "custom"
    chapter_pattern: str | None = None
    chapter_examples: list[str] = []
    # Browse-relative path of the source .txt, when it can be downloaded.
    download_path: str | None = None


class ChapterContent(BaseModel):
    index: int
    title: str
    total: int
    tokens: list[Token]
    prev: int | None = None
    next: int | None = None


class ProgressIn(BaseModel):
    position: int
    line: int | None = None  # null page top; v2 stores a stable character offset
    anchor_version: int = 2
    reset: bool = False  # force the bookmark to (position, line), even backward


class ProgressOut(BaseModel):
    ok: bool
    position: int
    line: int | None = None
    anchor_version: int = 2
    updated: str


class ChapterPatternPreviewIn(BaseModel):
    sample: str = ""
    pattern: str = ""


class ChapterPatternIn(BaseModel):
    pattern: str
    sample: str = ""


class ChapterPatternOut(BaseModel):
    pattern: str
    matches: int
    chapters: int = 0
    examples: list[str] = []
    selected_chapter: int = 0


class DefineEntry(BaseModel):
    pinyin: str
    defs: list[str]


class PerChar(BaseModel):
    char: str
    pinyin: str
    defs: list[str]


class DefineOut(BaseModel):
    word: str
    entries: list[DefineEntry]
    perChar: list[PerChar]
