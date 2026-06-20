import { useEffect, useRef } from "react";
import { api } from "../api/client";

// Intra-chapter scroll offset lives only in the browser (read.py knows chapters,
// not pixels). Chapter-level position is pushed to the shared backend file.
const scrollKey = (nid: string, idx: number) => `reader:scroll:${nid}:${idx}`;

export function saveScroll(nid: string, idx: number, offset: number) {
  try {
    localStorage.setItem(scrollKey(nid, idx), String(Math.round(offset)));
  } catch {
    /* storage full / disabled — non-fatal */
  }
}

export function loadScroll(nid: string, idx: number): number {
  const raw = localStorage.getItem(scrollKey(nid, idx));
  return raw ? Number(raw) || 0 : 0;
}

// Debounced, monotonic push of the current chapter to the backend. The backend
// also enforces monotonicity, but we avoid needless writes here.
export function useChapterProgress(nid: string | null, chapter: number) {
  const timer = useRef<number | null>(null);
  const lastSent = useRef<number>(-1);

  useEffect(() => {
    if (!nid || chapter < 0) return;
    if (chapter <= lastSent.current) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      lastSent.current = chapter;
      api.setProgress(nid, chapter).catch(() => {
        lastSent.current = -1; // allow a retry on the next change
      });
    }, 1500);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [nid, chapter]);
}
