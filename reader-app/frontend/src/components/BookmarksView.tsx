import { useState } from "react";
import { useReader } from "../store/reader";
import { useAuth } from "../store/auth";
import { chapterExcerpt } from "./Chapter";

// The bookmarks half of the drawer: where the reader left off, then the places
// they saved by hand, newest first. Picking any of them lands exactly where it
// was taken (chapter plus text anchor), the same way resuming does.
export function BookmarksView() {
  const bookmarks = useReader((s) => s.bookmarks);
  const goToLocation = useReader((s) => s.goToLocation);
  const addBookmark = useReader((s) => s.addBookmark);
  const removeBookmark = useReader((s) => s.removeBookmark);
  const novel = useReader((s) => s.novel);
  const chapters = useReader((s) => s.chapters);
  const progress = useReader((s) => s.progress);
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

  // The excerpt for a hand-saved bookmark is stored with it. The reading
  // position moves as you read, so cut its excerpt live from the chapter, which
  // is loaded whenever it's the one on screen.
  const progressContent = chapters[progress.chapter];
  const progressExcerpt = progressContent
    ? chapterExcerpt(progressContent.tokens, progress.line)
    : "";

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

      <ul className="toc-list bookmark-list">
        {/* Kept by the app rather than the reader, so there is no delete. */}
        <li className="bookmark-row is-progress-row">
          <button
            className="toc-row bookmark-jump is-progress"
            onClick={() => goToLocation(progress.chapter, progress.line)}
          >
            <span className="toc-ord">
              {String(progress.chapter + 1).padStart(2, "0")}
            </span>
            <span className="bookmark-text">
              <span className="bookmark-badge">Reading position</span>
              <span className="toc-title">
                {novel?.chapters[progress.chapter]?.title
                  ?? `Chapter ${progress.chapter + 1}`}
              </span>
              {progressExcerpt && (
                <span className="bookmark-excerpt">{progressExcerpt}</span>
              )}
            </span>
          </button>
        </li>

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

      {bookmarks.length === 0 && (
        <p className="toc-empty">
          No bookmarks saved yet. Use the button above to keep the place you're
          reading.
        </p>
      )}
    </>
  );
}
