import { useState } from "react";
import { useReader } from "../store/reader";
import { useAuth } from "../store/auth";

// The bookmarks half of the contents drawer: the places the reader saved by
// hand in this novel, newest first. Picking one lands the reader exactly where
// it was taken (chapter plus the text anchor), the same way resuming does.
export function BookmarksView() {
  const bookmarks = useReader((s) => s.bookmarks);
  const goToLocation = useReader((s) => s.goToLocation);
  const addBookmark = useReader((s) => s.addBookmark);
  const removeBookmark = useReader((s) => s.removeBookmark);
  const novel = useReader((s) => s.novel);
  const topChapter = useReader((s) => s.topChapter);
  const user = useAuth((s) => s.user);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!user) {
    return <p className="toc-empty">Log in to save bookmarks.</p>;
  }

  const run = (action: Promise<void>) => {
    setBusy(true);
    setError(null);
    action
      .catch((e: any) => setError(e?.message ?? "That didn't work"))
      .finally(() => setBusy(false));
  };

  return (
    <>
      <button
        className="btn-outline bookmark-add"
        disabled={busy}
        onClick={() => run(addBookmark())}
      >
        Bookmark this page · ch {topChapter + 1}
      </button>
      {error && <p className="toc-empty">{error}</p>}

      {bookmarks.length === 0 ? (
        <p className="toc-empty">
          No bookmarks yet. Save the place you're reading and it shows up here.
        </p>
      ) : (
        <ul className="toc-list bookmark-list">
          {bookmarks.map((bookmark) => (
            <li key={bookmark.id} className="bookmark-row">
              <button
                className="toc-row bookmark-jump"
                onClick={() => goToLocation(bookmark.chapter, bookmark.line)}
              >
                <span className="toc-ord">
                  {String(bookmark.chapter + 1).padStart(2, "0")}
                </span>
                <span className="bookmark-text">
                  <span className="toc-title">
                    {bookmark.chapter_title || `Chapter ${bookmark.chapter + 1}`}
                  </span>
                  {bookmark.excerpt && (
                    <span className="bookmark-excerpt">{bookmark.excerpt}</span>
                  )}
                  <span className="bookmark-date">
                    {bookmark.created.replace("T", " ").slice(0, 16)}
                  </span>
                </span>
              </button>
              <button
                className="bookmark-delete"
                disabled={busy}
                aria-label={`Delete bookmark in chapter ${bookmark.chapter + 1}`}
                onClick={() => run(removeBookmark(bookmark.id))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {novel && bookmarks.length > 0 && (
        <p className="toc-empty bookmark-hint">
          Bookmarks are saved to your account, per novel.
        </p>
      )}
    </>
  );
}
