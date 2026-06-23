import { create } from "zustand";
import { api, type ChapterContent, type NovelDetail } from "../api/client";

interface ReaderState {
  novel: NovelDetail | null;
  loading: boolean;
  error: string | null;
  // Parsed chapters cached by index for the open novel.
  chapters: Record<number, ChapterContent>;
  startPosition: number; // chapter to land on when opening
  furthest: number; // max chapter reached this session (drives the spine)
  current: number; // chapter under the reading line right now (drives the TOC)
  jumpTarget: number | null; // a TOC pick the reader should jump to, then clear

  // "discover" = the recommender landing page; "read" = the open novel.
  view: "discover" | "read";

  leftOpen: boolean;
  rightOpen: boolean;
  tocOpen: boolean;

  setView: (v: "discover" | "read") => void;
  openNovel: (nid: string, startAt?: number) => Promise<void>;
  loadChapter: (idx: number, annotate: boolean) => Promise<ChapterContent | null>;
  setFurthest: (idx: number) => void;
  setCurrent: (idx: number) => void;
  goToChapter: (idx: number) => void;
  clearJump: () => void;
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
  furthest: 0,
  current: 0,
  jumpTarget: null,
  view: "discover",
  leftOpen: false,
  rightOpen: false,
  tocOpen: false,

  setView: (v) => set({ view: v }),

  openNovel: async (nid, startAt) => {
    set({ loading: true, error: null, chapters: {}, novel: null, jumpTarget: null });
    try {
      const novel = await api.novel(nid);
      const start = Math.min(startAt ?? novel.position ?? 0, Math.max(novel.total - 1, 0));
      set({
        novel,
        startPosition: start,
        furthest: start,
        current: start,
        loading: false,
        view: "read",
        leftOpen: false,
        tocOpen: false,
      });
    } catch (e: any) {
      set({ loading: false, error: e?.message ?? "Could not open novel" });
    }
  },

  loadChapter: async (idx, annotate) => {
    const { novel, chapters } = get();
    if (!novel) return null;
    if (chapters[idx]) return chapters[idx];
    try {
      const content = await api.chapter(novel.nid, idx, annotate);
      set((s) => ({ chapters: { ...s.chapters, [idx]: content } }));
      return content;
    } catch {
      return null;
    }
  },

  setFurthest: (idx) =>
    set((s) => (idx > s.furthest ? { furthest: idx } : {})),
  setCurrent: (idx) => set((s) => (idx === s.current ? {} : { current: idx })),

  // Picked from the table of contents: jump there and close the drawer. The
  // ScrollReader watches jumpTarget, lands on the chapter, then clears it.
  goToChapter: (idx) =>
    set({ jumpTarget: idx, current: idx, tocOpen: false, leftOpen: false }),
  clearJump: () => set({ jumpTarget: null }),

  toggleLeft: (open) =>
    set((s) => ({ leftOpen: open ?? !s.leftOpen, tocOpen: false })),
  toggleRight: (open) => set((s) => ({ rightOpen: open ?? !s.rightOpen })),
  toggleToc: (open) =>
    set((s) => ({ tocOpen: open ?? !s.tocOpen, leftOpen: false })),
  closeNovel: () => set({ novel: null, chapters: {}, error: null, tocOpen: false }),
}));
