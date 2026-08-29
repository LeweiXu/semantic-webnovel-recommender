import { useReader } from "../store/reader";
import { useAuth } from "../store/auth";
import { PagedChapterList } from "./PagedChapterList";
import { BookmarksView } from "./BookmarksView";

// The left drawer for the open novel. Two ways to move around the same book:
// the table of contents (chapters lazy-load, so this is how you reach anywhere
// in it) and the reader's own saved bookmarks.
export function TocPanel() {
  const novel = useReader((s) => s.novel);
  const current = useReader((s) => s.current);
  const goToChapter = useReader((s) => s.goToChapter);
  const bookmarks = useReader((s) => s.bookmarks);
  const tab = useReader((s) => s.tocTab);
  const setTab = useReader((s) => s.setTocTab);
  const tocOpen = useReader((s) => s.tocOpen);
  const user = useAuth((s) => s.user);

  if (!novel) return null;

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="seal-glyph" aria-hidden>目</span>
        <h2 className="panel-title">Navigate</h2>
      </div>

      <div className="toc-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "contents"}
          className={`toc-tab${tab === "contents" ? " is-active" : ""}`}
          onClick={() => setTab("contents")}
        >
          Contents
        </button>
        <button
          role="tab"
          aria-selected={tab === "bookmarks"}
          className={`toc-tab${tab === "bookmarks" ? " is-active" : ""}`}
          onClick={() => setTab("bookmarks")}
        >
          Bookmarks
          {user && bookmarks.length > 0 && (
            <span className="toc-tab-count">{bookmarks.length}</span>
          )}
        </button>
      </div>

      <div className="toc-meta">
        {novel.title}
        <span className="toc-count">
          {tab === "contents"
            ? `${novel.total} chapters`
            : `${bookmarks.length} saved`}
        </span>
      </div>

      {tab === "contents" ? (
        <PagedChapterList
          chapters={novel.chapters}
          current={current}
          // Opening the drawer pages to the chapter being read and scrolls it
          // into view, so it never opens at chapter one of a 500-chapter book.
          revealCurrent={tocOpen}
          onSelect={goToChapter}
        />
      ) : (
        <BookmarksView />
      )}
    </div>
  );
}
