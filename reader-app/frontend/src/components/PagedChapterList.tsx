import { useEffect, useRef, useState } from "react";
import type { ChapterStub } from "../api/client";
import { formatCount } from "../format";

const PAGE_SIZE = 120;

interface Props {
  chapters: ChapterStub[];
  current?: number;
  className?: string;
  // Chapter lengths, set against the right edge of each row. Off by default:
  // the drawer is too narrow for a third column.
  showWords?: boolean;
  // While true, the list keeps the current chapter paged in and scrolled into
  // view. Pass the drawer's open state: each time it opens, the list returns to
  // wherever the reader is, undoing any manual paging from last time.
  revealCurrent?: boolean;
  onSelect: (chapter: number) => void;
}

export function PagedChapterList({
  chapters,
  current = -1,
  className = "",
  showWords = false,
  revealCurrent = false,
  onSelect,
}: Props) {
  const pages = Math.max(1, Math.ceil(chapters.length / PAGE_SIZE));
  const [page, setPage] = useState(() => (
    current >= 0 ? Math.floor(current / PAGE_SIZE) : 0
  ));

  useEffect(() => {
    if (current >= 0) setPage(Math.floor(current / PAGE_SIZE));
  }, [current, revealCurrent]);

  const safePage = Math.min(page, pages - 1);
  const start = safePage * PAGE_SIZE;
  const visible = chapters.slice(start, start + PAGE_SIZE);

  // Runs after the page above settles, so the active row is really rendered.
  const list = useRef<HTMLUListElement>(null);
  useEffect(() => {
    if (!revealCurrent || current < 0) return;
    list.current
      ?.querySelector<HTMLElement>(".toc-row.is-active")
      ?.scrollIntoView({ block: "center" });
  }, [revealCurrent, current, safePage]);

  return (
    <>
      <ul ref={list} className={`toc-list${className ? ` ${className}` : ""}`}>
        {visible.map((chapter) => (
          <li key={chapter.index}>
            <button
              className={`toc-row${chapter.index === current ? " is-active" : ""}`}
              onClick={() => onSelect(chapter.index)}
            >
              <span className="toc-ord">{String(chapter.index + 1).padStart(2, "0")}</span>
              <span className="toc-title">{chapter.title}</span>
              {showWords && chapter.words > 0 && (
                <span className="toc-words">{formatCount(chapter.words)}</span>
              )}
            </button>
          </li>
        ))}
      </ul>
      {pages > 1 && (
        <nav className="chapter-pages" aria-label="Chapter list pages">
          <button
            className="btn-outline"
            disabled={safePage === 0}
            onClick={() => setPage((value) => Math.max(0, value - 1))}
          >
            Previous
          </button>
          <span>
            {start + 1}–{Math.min(start + PAGE_SIZE, chapters.length)}
            {" of "}
            {chapters.length}
          </span>
          <button
            className="btn-outline"
            disabled={safePage >= pages - 1}
            onClick={() => setPage((value) => Math.min(pages - 1, value + 1))}
          >
            Next
          </button>
        </nav>
      )}
    </>
  );
}
