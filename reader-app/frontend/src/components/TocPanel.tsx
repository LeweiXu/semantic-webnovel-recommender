import { useReader } from "../store/reader";
import { PagedChapterList } from "./PagedChapterList";

// Table of contents for the open novel. Because chapters lazy-load, this is the
// way to jump anywhere in the book; picking a chapter lands the reader there.
export function TocPanel() {
  const novel = useReader((s) => s.novel);
  const current = useReader((s) => s.current);
  const goToChapter = useReader((s) => s.goToChapter);

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
      <PagedChapterList
        chapters={novel.chapters}
        current={current}
        onSelect={goToChapter}
      />
    </div>
  );
}
