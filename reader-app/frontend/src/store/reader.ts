import { create } from "zustand";
import { api, type ChapterContent, type NovelDetail } from "../api/client";
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
  jumpTarget: number | null; // a TOC pick the reader should jump to, then clear
  showSynopsis: boolean;
  chapterPatternOpen: boolean;

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
  setChromeVisible: (visible: boolean) => void;
  resetProgressToCurrent: () => Promise<void>;
  goToChapter: (idx: number) => void;
  clearJump: () => void;
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
  jumpTarget: null,
  showSynopsis: false,
  chapterPatternOpen: false,
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
  goToChapter: (idx) =>
    set((s) => {
      if (s.novel) writeUrl(readerPath(s.novel.slug, idx), false);
      return {
        jumpTarget: idx,
        current: idx,
        topChapter: idx,
        topLine: null,
        tocOpen: false,
        leftOpen: false,
        chromeVisible: true,
        showSynopsis: false,
      };
    }),
  clearJump: () => set({ jumpTarget: null }),
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
      tocOpen: false,
      chromeVisible: true,
      showSynopsis: false,
      chapterPatternOpen: false,
    });
  },
}));
