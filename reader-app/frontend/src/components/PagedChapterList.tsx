import { useEffect, useState } from "react";
import type { ChapterStub } from "../api/client";

const PAGE_SIZE = 120;

interface Props {
  chapters: ChapterStub[];
  current?: number;
  className?: string;
  onSelect: (chapter: number) => void;
}

export function PagedChapterList({
  chapters,
  current = -1,
  className = "",
  onSelect,
}: Props) {
  const pages = Math.max(1, Math.ceil(chapters.length / PAGE_SIZE));
  const [page, setPage] = useState(() => (
    current >= 0 ? Math.floor(current / PAGE_SIZE) : 0
  ));

  useEffect(() => {
    if (current >= 0) setPage(Math.floor(current / PAGE_SIZE));
  }, [current]);

  const safePage = Math.min(page, pages - 1);
  const start = safePage * PAGE_SIZE;
  const visible = chapters.slice(start, start + PAGE_SIZE);

  return (
    <>
      <ul className={`toc-list${className ? ` ${className}` : ""}`}>
        {visible.map((chapter) => (
          <li key={chapter.index}>
            <button
              className={`toc-row${chapter.index === current ? " is-active" : ""}`}
              onClick={() => onSelect(chapter.index)}
            >
              <span className="toc-ord">{String(chapter.index + 1).padStart(2, "0")}</span>
              <span className="toc-title">{chapter.title}</span>
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
