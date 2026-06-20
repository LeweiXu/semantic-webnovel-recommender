"""Pydantic response models for the reader API."""
from __future__ import annotations

from pydantic import BaseModel


class ReadingItem(BaseModel):
    url: str
    nid: str
    title: str
    author: str = ""
    category: str = ""
    position: int = 0
    total: int | None = None
    updated: str = ""


class SearchItem(BaseModel):
    url: str
    nid: str
    title: str
    author: str = ""
    category: str = ""
    downloaded: bool = False
    chapter_count: int | None = None


class ChapterStub(BaseModel):
    index: int
    title: str


class NovelDetail(BaseModel):
    url: str
    nid: str
    title: str
    author: str = ""
    category: str = ""
    synopsis: str = ""
    downloaded: bool = True
    total: int
    position: int = 0
    chapters: list[ChapterStub]


class Token(BaseModel):
    t: str
    py: str | None = None


class ChapterContent(BaseModel):
    index: int
    title: str
    total: int
    tokens: list[Token]
    prev: int | None = None
    next: int | None = None


class ProgressIn(BaseModel):
    position: int


class ProgressOut(BaseModel):
    ok: bool
    position: int
    updated: str


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
