import { useEffect, useRef } from "react";
import { useReader } from "../store/reader";

// Table of contents for the open novel. Because chapters lazy-load, this is the
// way to jump anywhere in the book; picking a chapter lands the reader there.
export function TocPanel() {
  const novel = useReader((s) => s.novel);
  const current = useReader((s) => s.current);
  const tocOpen = useReader((s) => s.tocOpen);
  const goToChapter = useReader((s) => s.goToChapter);
  const activeRef = useRef<HTMLButtonElement>(null);

  // Keep the chapter you're on in view whenever the drawer opens.
  useEffect(() => {
    if (tocOpen) activeRef.current?.scrollIntoView({ block: "center" });
  }, [tocOpen, current]);

  if (!novel) return null;

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="seal-glyph" aria-hidden>目</span>
        <h2 className="panel-title">Contents</h2>
      </div>
      <div className="toc-meta">
        {novel.title}
        <span className="toc-count">{novel.total} chapters</span>
      </div>
      <ul className="toc-list">
        {novel.chapters.map((c) => (
          <li key={c.index}>
            <button
              ref={c.index === current ? activeRef : undefined}
              className={`toc-row${c.index === current ? " is-active" : ""}`}
              onClick={() => goToChapter(c.index)}
            >
              <span className="toc-ord">{String(c.index + 1).padStart(2, "0")}</span>
              <span className="toc-title">{c.title}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
