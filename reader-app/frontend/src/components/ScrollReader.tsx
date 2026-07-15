import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useReader } from "../store/reader";
import { useSettings } from "../store/settings";
import { useReadingProgress } from "../hooks/useProgress";
import { Chapter, splitParagraphs } from "./Chapter";
import { RubyText } from "./RubyText";
import { DefinitionPopup, type PopupTarget } from "./DefinitionPopup";
import { readerPath, writeUrl } from "../routing";

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

function relativeTop(el: HTMLElement, root: HTMLElement): number {
  return el.getBoundingClientRect().top - root.getBoundingClientRect().top;
}

function renderedLineHeight(paragraph: HTMLElement): number {
  const computed = window.getComputedStyle(paragraph);
  const lineHeight = Number.parseFloat(computed.lineHeight);
  if (Number.isFinite(lineHeight) && lineHeight > 0) return lineHeight;
  return Number.parseFloat(computed.fontSize) * 1.5;
}

function renderedLineCount(paragraph: HTMLElement): number {
  return Math.max(1, Math.round(paragraph.getBoundingClientRect().height / renderedLineHeight(paragraph)));
}

// Return null until the body itself reaches the viewport top. Once reading has
// started, wrapped visual lines are numbered from zero across the chapter.
function lineAtViewportTop(section: HTMLElement, root: HTMLElement): number | null {
  const paragraphs = section.querySelectorAll<HTMLElement>(".chapter-body > p");
  const viewportTop = root.getBoundingClientRect().top + 1;
  let linesBefore = 0;

  for (const paragraph of paragraphs) {
    const rect = paragraph.getBoundingClientRect();
    const count = renderedLineCount(paragraph);
    if (rect.bottom <= viewportTop) {
      linesBefore += count;
      continue;
    }
    if (rect.top > viewportTop) return linesBefore === 0 ? null : linesBefore;
    const within = Math.min(
      count - 1,
      Math.max(0, Math.floor((viewportTop - rect.top) / renderedLineHeight(paragraph))),
    );
    return linesBefore + within;
  }
  return linesBefore > 0 ? linesBefore - 1 : null;
}

function scrollTopForLine(section: HTMLElement, root: HTMLElement, line: number): number {
  const paragraphs = section.querySelectorAll<HTMLElement>(".chapter-body > p");
  let remaining = Math.max(0, line);
  let lastOffset = 0;

  for (const paragraph of paragraphs) {
    const count = renderedLineCount(paragraph);
    const lineHeight = renderedLineHeight(paragraph);
    if (remaining < count) {
      return root.scrollTop + relativeTop(paragraph, root) + remaining * lineHeight;
    }
    lastOffset = root.scrollTop + relativeTop(paragraph, root) + (count - 1) * lineHeight;
    remaining -= count;
  }
  return lastOffset;
}

export function ScrollReader() {
  const novel = useReader((s) => s.novel)!;
  const chapters = useReader((s) => s.chapters);
  const loadChapter = useReader((s) => s.loadChapter);
  const startPosition = useReader((s) => s.startPosition);
  const startLine = useReader((s) => s.startLine);
  const current = useReader((s) => s.current);
  const setCurrent = useReader((s) => s.setCurrent);
  const setFurthest = useReader((s) => s.setFurthest);
  const setTop = useReader((s) => s.setTop);
  const setChromeVisible = useReader((s) => s.setChromeVisible);
  const topChapter = useReader((s) => s.topChapter);
  const topLine = useReader((s) => s.topLine);
  const jumpTarget = useReader((s) => s.jumpTarget);
  const clearJump = useReader((s) => s.clearJump);
  const pinyin = useSettings((s) => s.pinyin);
  const synopsisPinyin = useSettings((s) => s.synopsisPinyin);

  const total = novel.total;

  // Contiguous, sorted indices of the mounted chapters. Every entry has its
  // content cached before it is added, so heights never shift after mount.
  const [loaded, setLoaded] = useState<number[]>([]);
  const [popup, setPopup] = useState<PopupTarget | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const sections = useRef<Map<number, HTMLElement>>(new Map());
  const fetching = useRef<Set<number>>(new Set());
  // When the window changes above the viewport (prepend or drop-from-top), the
  // content above shifts; we pin the current chapter's on-screen position and
  // correct scrollTop after layout so the reader never jumps.
  const anchor = useRef<{ idx: number; top: number } | null>(null);
  // After landing on a chapter (open or TOC jump), pin the page once it renders.
  // A null pending line means page top; otherwise restore that body line.
  const pendingLand = useRef<number | null>(null);
  const pendingLine = useRef<number | null>(null);
  // True while a land is in flight, so the scroll handler for the previous
  // window can't reinterpret the current chapter mid-jump.
  const landing = useRef(false);

  // Persist the exact top-of-page position (chapter + paragraph) as we read.
  useReadingProgress(novel.nid, topChapter, topLine);

  const onWord = (word: string, el: HTMLElement) =>
    setPopup({ word, rect: el.getBoundingClientRect() });

  // Fetch a chapter's annotated body (deduped by the store cache).
  const ensure = (idx: number) =>
    chapters[idx] ? Promise.resolve(chapters[idx]) : loadChapter(idx, true);

  // Element edges relative to the scroll container's visible top.
  const relTop = (el: HTMLElement) =>
    relativeTop(el, scrollRef.current!);
  const relBottom = (el: HTMLElement) =>
    el.getBoundingClientRect().bottom - scrollRef.current!.getBoundingClientRect().top;

  // Land on a chapter: mount only it (nothing above), then pin the page top.
  // `synopsis` lands on the synopsis start; `line` restores a rendered line.
  const landOn = (idx: number, opts: { line?: number | null } = {}) => {
    landing.current = true;
    setChromeVisible(true);
    ensure(idx).then(() => {
      anchor.current = null;
      pendingLand.current = idx;
      pendingLine.current = opts.line ?? null;
      setLoaded([idx]);
    });
  };

  // On mount (the component is keyed by novel id, so this is once per novel),
  // open on the bookmark. A null line always means the top of the page; a saved
  // body line restores the exact reading position.
  useEffect(() => {
    landOn(startPosition, { line: startLine });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A table-of-contents pick: land on that chapter's top and advance the spine
  // if it's ahead of where we are.
  useEffect(() => {
    if (jumpTarget === null) return;
    const idx = jumpTarget;
    clearJump();
    setFurthest(idx);
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
      const line = pendingLine.current;
      pendingLine.current = null;
      const el = sections.current.get(idx);
      root.scrollTop = el && line !== null ? scrollTopForLine(el, root, line) : 0;
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
    let lastScrollTop = root.scrollTop;
    let directionDelta = 0;

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
      }
      const curEl = sections.current.get(cur);

      // Record the chapter + rendered text line at the very top of the page —
      // this is the exact-resume bookmark (see useReadingProgress).
      let topC = loaded[0];
      for (const idx of loaded) {
        const el = sections.current.get(idx);
        if (el && relTop(el) <= 4) topC = idx;
      }
      let topL: number | null = null;
      const topEl = sections.current.get(topC);
      if (topEl) topL = lineAtViewportTop(topEl, root);
      setTop(topC, topL);
      writeUrl(readerPath(novel.nid, topC, topL), true);

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
      const nextScrollTop = root.scrollTop;
      const delta = nextScrollTop - lastScrollTop;
      if (delta !== 0) {
        if (Math.sign(delta) !== Math.sign(directionDelta)) directionDelta = 0;
        directionDelta += delta;
      }
      if (nextScrollTop <= 2) {
        directionDelta = 0;
        setChromeVisible(true);
      } else if (directionDelta > 12) {
        directionDelta = 0;
        setChromeVisible(false);
      } else if (directionDelta < -12) {
        directionDelta = 0;
        setChromeVisible(true);
      }
      lastScrollTop = nextScrollTop;
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
  }, [loaded, current, novel.nid, total, setChromeVisible, setCurrent, setFurthest, setTop]);

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
              {synopsisPinyin && novel.synopsis_tokens?.length ? (
                <div className="synopsis-body has-pinyin">
                  {splitParagraphs(novel.synopsis_tokens).map((para, pi) => (
                    <p key={pi}>
                      {para.map((tok, ti) => (
                        <RubyText key={ti} token={tok} pinyin onWord={onWord} />
                      ))}
                    </p>
                  ))}
                </div>
              ) : (
                <p className="synopsis-body">{novel.synopsis}</p>
              )}
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
