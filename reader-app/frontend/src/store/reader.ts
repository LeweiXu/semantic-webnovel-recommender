import { create } from "zustand";
import { api, type Bookmark, type ChapterContent, type NovelDetail } from "../api/client";
import { useAuth } from "./auth";
import { chapterExcerpt } from "../components/Chapter";
import { readerPath, writeUrl } from "../routing";

interface OpenLocation {
  chapter?: number;
  line?: number | null;
}

let openSequence = 0;

interface ReaderState {
  novel: NovelDetail | null;
  loading: boolean;
  error: string | null;
  // Parsed chapters cached by index for the open novel.
  chapters: Record<number, ChapterContent>;
  startPosition: number; // chapter to land on when opening
  startLine: number | null; // null page top; otherwise a stable character anchor
  startAnchorVersion: number;
  furthest: number; // max chapter reached this session (drives the spine)
  current: number; // chapter under the reading line right now (drives the TOC)
  topChapter: number; // chapter at the very top of the page (for exact resume)
  topLine: number | null; // null page top; otherwise a stable character anchor
  chapterPct: number; // 0-100 through the chapter being read (drives the spine)
  jumpTarget: number | null; // a TOC pick the reader should jump to, then clear
  jumpLine: number | null; // where inside jumpTarget to land; null = its top
  showSynopsis: boolean;
  chapterPatternOpen: boolean;
  // Hand-saved places in the open novel, newest first (empty when logged out).
  bookmarks: Bookmark[];
  tocTab: "contents" | "bookmarks";

  // "discover" = the recommender landing page; "library" = the reading shelf;
  // "novel" = a novel's landing page (tags, synopsis, chapter list);
  // "read" = the open novel.
  view: "discover" | "read" | "library" | "novel";

  leftOpen: boolean;
  rightOpen: boolean;
  tocOpen: boolean;
  chromeVisible: boolean;

  setView: (v: "discover" | "read" | "library" | "novel") => void;
  openNovel: (
    nid: string,
    location?: OpenLocation,
    updateUrl?: boolean | "replace",
  ) => Promise<void>;
  loadChapter: (idx: number, annotate: boolean) => Promise<ChapterContent | null>;
  setFurthest: (idx: number) => void;
  setCurrent: (idx: number) => void;
  setTop: (chapter: number, line: number | null) => void;
  setChapterPct: (pct: number) => void;
  setChromeVisible: (visible: boolean) => void;
  resetProgressToCurrent: () => Promise<void>;
  goToChapter: (idx: number) => void;
  goToLocation: (chapter: number, line: number | null) => void;
  clearJump: () => void;
  loadBookmarks: () => Promise<void>;
  addBookmark: () => Promise<void>;
  removeBookmark: (id: string) => Promise<void>;
  setTocTab: (tab: "contents" | "bookmarks") => void;
  openChapterPattern: () => void;
  closeChapterPattern: () => void;
  toggleLeft: (open?: boolean) => void;
  toggleRight: (open?: boolean) => void;
  toggleToc: (open?: boolean) => void;
  closeNovel: () => void;
}

export const useReader = create<ReaderState>((set, get) => ({
  novel: null,
  loading: false,
  error: null,
  chapters: {},
  startPosition: 0,
  startLine: null,
  startAnchorVersion: 2,
  furthest: 0,
  current: 0,
  topChapter: 0,
  topLine: null,
  chapterPct: 0,
  jumpTarget: null,
  jumpLine: null,
  showSynopsis: false,
  chapterPatternOpen: false,
  bookmarks: [],
  tocTab: "contents",
  view: "discover",
  leftOpen: false,
  rightOpen: false,
  tocOpen: false,
  chromeVisible: true,

  setView: (v) => {
    if (v !== "read") openSequence += 1;
    set({ view: v, chapterPatternOpen: false });
  },

  openNovel: async (nid, location, updateUrl = true) => {
    const request = ++openSequence;
    set({
      loading: true,
      error: null,
      chapters: {},
      novel: null,
      jumpTarget: null,
      jumpLine: null,
      bookmarks: [],
      tocTab: "contents",
      chapterPatternOpen: false,
      view: "read",
      chromeVisible: true,
    });
    try {
      const novel = await api.novel(nid);
      if (request !== openSequence) return;
      const start = Math.min(location?.chapter ?? novel.position ?? 0, Math.max(novel.total - 1, 0));
      // Resolve where to land vertically within the chapter:
      //  - no location at all: resume the account's fine-grained bookmark.
      //  - a chapter but no `line` field (e.g. a reader URL on reload): restore the
      //    saved line if we're opening the bookmarked chapter, else the top.
      //  - an explicit `line` (null = page top, or a saved body line): use it.
      let startLine: number | null;
      let startAnchorVersion = 2;
      if (location === undefined) {
        startLine = novel.line;
        startAnchorVersion = novel.anchor_version;
      } else if (location.line === undefined) {
        startLine = start === (novel.position ?? 0) ? novel.line : null;
        startAnchorVersion = start === (novel.position ?? 0) ? novel.anchor_version : 2;
      } else {
        startLine = location.line;
      }
      // Canonicalise to the readable slug, even if opened via a legacy/base64 id.
      if (updateUrl) writeUrl(readerPath(novel.slug, start), updateUrl === "replace");
      set({
        novel,
        startPosition: start,
        startLine,
        startAnchorVersion,
        furthest: start,
        current: start,
        topChapter: start,
        topLine: startLine,
        chapterPct: 0,
        showSynopsis: (
          location === undefined
          && start === 0
          && startLine === null
          && Boolean(novel.synopsis)
        ),
        loading: false,
        view: "read",
        chromeVisible: true,
        leftOpen: false,
        tocOpen: false,
      });
      void get().loadBookmarks();
    } catch (e: any) {
      if (request !== openSequence) return;
      set({ loading: false, error: e?.message ?? "Could not open novel" });
    }
  },

  loadChapter: async (idx, annotate) => {
    const { novel, chapters } = get();
    if (!novel) return null;
    if (chapters[idx]) return chapters[idx];
    try {
      const content = await api.chapter(novel.slug, idx, annotate);
      set((s) => ({ chapters: { ...s.chapters, [idx]: content } }));
      return content;
    } catch {
      return null;
    }
  },

  setFurthest: (idx) =>
    set((s) => (idx > s.furthest ? { furthest: idx } : {})),
  setCurrent: (idx) => set((s) => (idx === s.current ? {} : { current: idx })),
  setTop: (chapter, line) =>
    set((s) =>
      s.topChapter === chapter && s.topLine === line ? {} : { topChapter: chapter, topLine: line },
    ),
  // Whole integer percents only: the reader recomputes this on every scroll
  // frame, and rounding keeps that from re-rendering the spine continuously.
  setChapterPct: (pct) => set((s) => (s.chapterPct === pct ? {} : { chapterPct: pct })),
  setChromeVisible: (visible) =>
    set((s) => (s.chromeVisible === visible ? {} : { chromeVisible: visible })),

  // Settings "reset progress to here": force the bookmark back to the top of the
  // current page (chapter + text anchor), even if that's earlier than the furthest read.
  resetProgressToCurrent: async () => {
    const { novel, topChapter, topLine } = get();
    if (!novel) return;
    set({ furthest: topChapter, current: topChapter });
    try {
      await api.setProgress(novel.slug, topChapter, topLine, true);
    } catch {
      /* leave the local spine where we set it; a later scroll will re-sync */
    }
  },

  // Picked from the table of contents: jump there and close the drawer. The
  // ScrollReader watches jumpTarget, lands on the chapter, then clears it.
  goToChapter: (idx) => get().goToLocation(idx, null),

  // Also the plain TOC jump (with a null line), so both paths land identically.
  goToLocation: (chapter, line) =>
    set((s) => {
      if (s.novel) writeUrl(readerPath(s.novel.slug, chapter), false);
      return {
        jumpTarget: chapter,
        jumpLine: line,
        current: chapter,
        topChapter: chapter,
        topLine: line,
        chapterPct: 0,
        tocOpen: false,
        leftOpen: false,
        chromeVisible: true,
        showSynopsis: false,
      };
    }),
  clearJump: () => set({ jumpTarget: null, jumpLine: null }),

  // Bookmarks live on the account, so a logged-out reader simply has none. Every
  // mutation takes the server's list as the truth rather than patching locally.
  loadBookmarks: async () => {
    const { novel } = get();
    if (!novel || !useAuth.getState().user) return;
    const slug = novel.slug;
    try {
      const bookmarks = await api.bookmarks(slug);
      if (get().novel?.slug === slug) set({ bookmarks });
    } catch {
      /* a failed load just leaves the list empty */
    }
  },

  // Save the position the reader is showing right now: the chapter and text
  // anchor at the top of the page, the same pair the reading bookmark uses.
  addBookmark: async () => {
    const { novel, chapters, topChapter, topLine } = get();
    if (!novel || !useAuth.getState().user) return;
    const slug = novel.slug;
    const content = chapters[topChapter];
    const bookmarks = await api.addBookmark(slug, {
      chapter: topChapter,
      line: topLine,
      chapter_title: novel.chapters[topChapter]?.title ?? "",
      excerpt: content ? chapterExcerpt(content.tokens, topLine) : "",
    });
    if (get().novel?.slug === slug) set({ bookmarks });
  },

  removeBookmark: async (id) => {
    const { novel } = get();
    if (!novel) return;
    const slug = novel.slug;
    const bookmarks = await api.deleteBookmark(slug, id);
    if (get().novel?.slug === slug) set({ bookmarks });
  },

  setTocTab: (tab) => set({ tocTab: tab }),
  openChapterPattern: () => set({ chapterPatternOpen: true, rightOpen: false }),
  closeChapterPattern: () => set({ chapterPatternOpen: false }),

  toggleLeft: (open) =>
    set((s) => ({ leftOpen: open ?? !s.leftOpen, tocOpen: false })),
  toggleRight: (open) => set((s) => ({ rightOpen: open ?? !s.rightOpen })),
  toggleToc: (open) =>
    set((s) => ({ tocOpen: open ?? !s.tocOpen, leftOpen: false })),
  closeNovel: () => {
    openSequence += 1;
    set({
      novel: null,
      chapters: {},
      error: null,
      bookmarks: [],
      tocTab: "contents",
      tocOpen: false,
      chromeVisible: true,
      showSynopsis: false,
      chapterPatternOpen: false,
    });
  },
}));
