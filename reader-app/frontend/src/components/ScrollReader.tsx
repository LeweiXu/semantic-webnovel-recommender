import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useReader } from "../store/reader";
import { useSettings } from "../store/settings";
import { loadScroll, saveScroll, useChapterProgress } from "../hooks/useProgress";
import { Chapter } from "./Chapter";
import { DefinitionPopup, type PopupTarget } from "./DefinitionPopup";

// Forward-only windowed scroll. The chapter you open/resume/jump to is mounted
// alone at the very top — nothing above it. As you near its end the next chapter
// loads below; once a chapter has scrolled fully out of view it is unmounted.
// Keeping the DOM tiny and never prepending above the read position is what makes
// opening fast and landing on a chapter exact (no backward drift).
//
// How early the next chapter loads: when the end of the last mounted chapter
// comes within this many viewport heights below the fold.
const LOOKAHEAD_VH = 1.5;
// How far a passed chapter must clear the top of the viewport before it is
// unmounted — a margin so the reflow is never visible.
const DROP_MARGIN_VH = 0.25;

export function ScrollReader() {
  const novel = useReader((s) => s.novel)!;
  const chapters = useReader((s) => s.chapters);
  const loadChapter = useReader((s) => s.loadChapter);
  const startPosition = useReader((s) => s.startPosition);
  const current = useReader((s) => s.current);
  const setCurrent = useReader((s) => s.setCurrent);
  const setFurthest = useReader((s) => s.setFurthest);
  const jumpTarget = useReader((s) => s.jumpTarget);
  const clearJump = useReader((s) => s.clearJump);
  const pinyin = useSettings((s) => s.pinyin);

  const total = novel.total;

  // Contiguous, sorted indices of the mounted chapters. Every entry has its
  // content cached before it is added, so heights never shift after mount.
  const [loaded, setLoaded] = useState<number[]>([]);
  const [reportable, setReportable] = useState(-1);
  const [popup, setPopup] = useState<PopupTarget | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const sections = useRef<Map<number, HTMLElement>>(new Map());
  const fetching = useRef<Set<number>>(new Set());
  // When the window changes above the viewport (prepend or drop-from-top), the
  // content above shifts; we pin the current chapter's on-screen position and
  // correct scrollTop after layout so the reader never jumps.
  const anchor = useRef<{ idx: number; top: number } | null>(null);
  // After landing on a chapter (open or TOC jump), put it at the top and restore
  // the saved intra-chapter offset, once it has actually rendered.
  const pendingLand = useRef<number | null>(null);
  // True while a land is in flight, so the scroll handler for the previous
  // window can't reinterpret the current chapter mid-jump.
  const landing = useRef(false);

  // Only report chapters at/past where the novel was opened, so merely opening
  // (or re-reading backward) never rewrites the shared bookmark.
  useChapterProgress(novel.nid, reportable);

  const onWord = (word: string, el: HTMLElement) =>
    setPopup({ word, rect: el.getBoundingClientRect() });

  // Fetch a chapter's annotated body (deduped by the store cache).
  const ensure = (idx: number) =>
    chapters[idx] ? Promise.resolve(chapters[idx]) : loadChapter(idx, true);

  // Element edges relative to the scroll container's visible top.
  const relTop = (el: HTMLElement) =>
    el.getBoundingClientRect().top - scrollRef.current!.getBoundingClientRect().top;
  const relBottom = (el: HTMLElement) =>
    el.getBoundingClientRect().bottom - scrollRef.current!.getBoundingClientRect().top;

  // Land on a chapter: mount only it (nothing above), then jump to its top +
  // saved offset. Used on open and on table-of-contents jumps.
  const landOn = (idx: number) => {
    landing.current = true;
    ensure(idx).then(() => {
      anchor.current = null;
      pendingLand.current = idx;
      setLoaded([idx]);
    });
  };

  // On mount (the component is keyed by novel id, so this is once per novel),
  // open on the bookmarked chapter.
  useEffect(() => {
    landOn(startPosition);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A table-of-contents pick: land on it and advance the bookmark/spine if it is
  // ahead of where we opened.
  useEffect(() => {
    if (jumpTarget === null) return;
    const idx = jumpTarget;
    clearJump();
    setFurthest(idx);
    if (idx > startPosition) setReportable(idx);
    landOn(idx);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jumpTarget]);

  // Apply pending anchor correction / land restore after the window renders.
  useLayoutEffect(() => {
    const root = scrollRef.current;
    if (!root) return;

    if (pendingLand.current !== null) {
      const idx = pendingLand.current;
      pendingLand.current = null;
      const el = sections.current.get(idx);
      if (el) {
        // Chapter 0 keeps the synopsis above it; everything else sits at the top.
        const top = idx === 0 ? 0 : relTop(el) + root.scrollTop;
        root.scrollTop = top + loadScroll(novel.nid, idx);
      }
      anchor.current = null;
      landing.current = false;
      return;
    }

    if (anchor.current) {
      const el = sections.current.get(anchor.current.idx);
      if (el) root.scrollTop += relTop(el) - anchor.current.top;
      anchor.current = null;
    }
  }, [loaded, novel.nid]);

  // Scroll handler: track the current chapter, persist intra-chapter offset, and
  // slide the mounted window. Re-bound when the window or current chapter change
  // so it always closes over fresh values.
  useEffect(() => {
    const root = scrollRef.current;
    if (!root || loaded.length === 0) return;
    let raf = 0;

    const tick = () => {
      raf = 0;
      if (landing.current) return; // a jump is settling — don't fight it
      const vh = root.clientHeight;
      const line = vh * 0.3; // reading line, relative to viewport top

      // Current chapter = the lowest mounted chapter whose top has passed the line.
      let cur = loaded[0];
      for (const idx of loaded) {
        const el = sections.current.get(idx);
        if (el && relTop(el) <= line) cur = idx;
      }
      if (cur !== current) {
        setCurrent(cur);
        setFurthest(cur);
        if (cur > startPosition) setReportable(cur);
      }

      // Persist how far we've scrolled past the current chapter's top.
      const curEl = sections.current.get(cur);
      if (curEl) saveScroll(novel.nid, cur, -relTop(curEl));

      const lo = loaded[0];
      const hi = loaded[loaded.length - 1];

      // Forward-only: unmount the top chapter once it has scrolled fully clear of
      // the viewport. Anchor on the current chapter so the page never jumps.
      if (lo < cur && curEl) {
        const firstEl = sections.current.get(lo);
        if (firstEl && relBottom(firstEl) < -vh * DROP_MARGIN_VH) {
          anchor.current = { idx: cur, top: relTop(curEl) };
          setLoaded((w) => w.filter((i) => i > lo));
          return;
        }
      }

      // Load the next chapter as the end of the last one nears the fold.
      if (hi < total - 1 && !fetching.current.has(hi + 1)) {
        const lastEl = sections.current.get(hi);
        if (lastEl && relBottom(lastEl) < vh * (1 + LOOKAHEAD_VH)) {
          const next = hi + 1;
          fetching.current.add(next);
          ensure(next)
            .then(() => setLoaded((w) => (w.includes(next) ? w : [...w, next])))
            .finally(() => fetching.current.delete(next));
        }
      }
    };

    const onScroll = () => {
      if (raf) return;
      raf = window.requestAnimationFrame(tick);
    };
    root.addEventListener("scroll", onScroll, { passive: true });
    // Reconcile once on (re)bind so neighbours prefetch on open and the window
    // keeps converging even if the current chapter never triggers a scroll.
    tick();
    return () => {
      root.removeEventListener("scroll", onScroll);
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, [loaded, current, novel.nid, total, startPosition, setCurrent, setFurthest]);

  const setRef = (idx: number) => (el: HTMLElement | null) => {
    if (el) sections.current.set(idx, el);
    else sections.current.delete(idx);
  };

  return (
    <div className="scroll-root" ref={scrollRef}>
      {loaded.length === 0 ? (
        <div className="stage-note">Opening…</div>
      ) : (
        <div className="reading-column">
          {novel.synopsis && loaded[0] === 0 && (
            <section className="synopsis">
              <h1 className="novel-title">{novel.title}</h1>
              {novel.author && <p className="novel-author">{novel.author}</p>}
              <p className="synopsis-body">{novel.synopsis}</p>
            </section>
          )}
          {loaded.map((idx) => {
            const content = chapters[idx];
            const stub = novel.chapters[idx];
            return (
              <section key={idx} data-idx={idx} ref={setRef(idx)} className="chapter-slot">
                {content ? (
                  <Chapter content={content} pinyin={pinyin} onWord={onWord} />
                ) : (
                  <div className="chapter-placeholder">
                    <span className="chapter-ord">{String(idx + 1).padStart(2, "0")}</span>
                    <span className="chapter-title-muted">{stub?.title}</span>
                  </div>
                )}
              </section>
            );
          })}
          {loaded[loaded.length - 1] === total - 1 && (
            <footer className="reading-end">· 完 ·</footer>
          )}
        </div>
      )}
      <DefinitionPopup target={popup} onClose={() => setPopup(null)} />
    </div>
  );
}
