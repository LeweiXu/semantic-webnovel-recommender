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


class SearchItem(BaseModel):
    url: str
    nid: str
    slug: str | None = None
    title: str
    author: str = ""
    category: str = ""
    downloaded: bool = False
    chapter_count: int | None = None


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
    chapters: list[ChapterStub]


class ChapterContent(BaseModel):
    index: int
    title: str
    total: int
    tokens: list[Token]
    prev: int | None = None
    next: int | None = None


class ProgressIn(BaseModel):
    position: int
    line: int | None = None  # null page top; 0+ rendered body line
    reset: bool = False  # force the bookmark to (position, line), even backward


class ProgressOut(BaseModel):
    ok: bool
    position: int
    line: int | None = None
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
