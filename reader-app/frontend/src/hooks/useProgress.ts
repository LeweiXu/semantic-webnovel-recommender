import { useCallback, useEffect, useRef } from "react";
import { api } from "../api/client";
import { useAuth } from "../store/auth";

// Debounced push of the reader's exact position (chapter + the rendered line at
// top of the page) for the signed-in user. We send the current top whenever it
// changes; the backend keeps it monotonic (never rewinds on a re-read or a jump
// back), so sending a lower position is a harmless no-op. The "reset to here"
// control force-writes separately.
export function useReadingProgress(nid: string | null, chapter: number, line: number | null) {
  const username = useAuth((s) => s.user?.username ?? null);
  const timer = useRef<number | null>(null);
  const lastSent = useRef<string>("");
  const latest = useRef<{ nid: string; chapter: number; line: number } | null>(null);

  const flush = useCallback((keepalive = false) => {
    const position = latest.current;
    if (!position) return;
    const key = `${position.chapter}:${position.line}`;
    if (key === lastSent.current) return;
    lastSent.current = key;
    api.setProgress(position.nid, position.chapter, position.line, false, keepalive).catch(() => {
      if (lastSent.current === key) lastSent.current = "";
    });
  }, []);

  useEffect(() => {
    lastSent.current = "";
    if (timer.current) window.clearTimeout(timer.current);
    const onPageHide = () => flush(true);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
      if (timer.current) window.clearTimeout(timer.current);
      flush(true);
      latest.current = null;
    };
  }, [username, nid, flush]);

  useEffect(() => {
    // A null line means the chapter body has not reached the viewport top. Do
    // not create a heading bookmark; opening that chapter should remain page-top.
    if (!username || !nid || chapter < 0 || line === null) return;
    latest.current = { nid, chapter, line };
    const key = `${chapter}:${line}`;
    if (key === lastSent.current) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      flush();
    }, 1000);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [username, nid, chapter, line, flush]);
}
