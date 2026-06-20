import { useEffect, useMemo, useState } from "react";
import { api, type ReadingItem, type SearchItem } from "../api/client";
import { useReader } from "../store/reader";
import { DownloadDialog } from "./DownloadDialog";

const isNovelUrl = (s: string) =>
  /^https?:\/\/(www\.)?52shuku\.net\/[^/]+\/.+\.html$/i.test(s.trim());

function progressPct(position: number, total: number | null): number {
  if (!total) return 0;
  return Math.min(100, Math.round(((position + 1) / total) * 100));
}

export function LibraryPanel() {
  const openNovel = useReader((s) => s.openNovel);
  const currentNid = useReader((s) => s.novel?.nid);
  const leftOpen = useReader((s) => s.leftOpen);

  const [reading, setReading] = useState<ReadingItem[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchItem[]>([]);
  const [searching, setSearching] = useState(false);

  const refreshReading = () => api.reading().then(setReading).catch(() => {});
  // Refresh whenever the drawer opens so positions reflect reading done since
  // the last time it was shown (progress advances in the background while you
  // read, and is written to the shared bookmark file).
  useEffect(() => {
    if (leftOpen) refreshReading();
  }, [leftOpen]);

  const pastedUrl = useMemo(() => (isNovelUrl(query) ? query.trim() : null), [query]);

  useEffect(() => {
    const q = query.trim();
    if (!q || pastedUrl) {
      setResults([]);
      return;
    }
    let alive = true;
    setSearching(true);
    const handle = window.setTimeout(() => {
      api
        .search(q)
        .then((r) => alive && setResults(r))
        .catch(() => alive && setResults([]))
        .finally(() => alive && setSearching(false));
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(handle);
    };
  }, [query, pastedUrl]);

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="seal-glyph" aria-hidden>读</span>
        <h2 className="panel-title">Library</h2>
      </div>

      <input
        className="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search titles, or paste a 52shuku link"
        spellCheck={false}
        autoComplete="off"
      />

      {pastedUrl && (
        <DownloadDialog
          url={pastedUrl}
          onDone={(nid) => {
            setQuery("");
            refreshReading();
            openNovel(nid);
          }}
        />
      )}

      {!pastedUrl && query.trim() && (
        <section className="panel-section">
          <div className="section-label">{searching ? "Searching…" : "Results"}</div>
          <ul className="novel-list">
            {results.map((r) => (
              <li key={r.nid}>
                <button
                  className="novel-row"
                  disabled={!r.downloaded}
                  onClick={() => r.downloaded && openNovel(r.nid)}
                  title={r.downloaded ? "" : "Not downloaded yet"}
                >
                  <span className="novel-row-title">{r.title}</span>
                  <span className="novel-row-meta">
                    {r.author || "—"}
                    {!r.downloaded && <em className="tag-meta"> · metadata</em>}
                  </span>
                </button>
              </li>
            ))}
            {!searching && results.length === 0 && (
              <li className="empty">No matches.</li>
            )}
          </ul>
        </section>
      )}

      {!query.trim() && (
        <section className="panel-section">
          <div className="section-label">Currently reading</div>
          <ul className="novel-list">
            {reading.map((r) => {
              const pct = progressPct(r.position, r.total);
              return (
                <li key={r.nid}>
                  <button
                    className={`novel-row${currentNid === r.nid ? " is-active" : ""}`}
                    onClick={() => openNovel(r.nid, r.position)}
                  >
                    <span className="novel-row-title">{r.title}</span>
                    <span className="novel-row-meta">
                      {r.author || "—"} · ch {r.position + 1}
                      {r.total ? `/${r.total}` : ""}
                    </span>
                    <span className="rowbar" aria-hidden>
                      <span className="rowbar-fill" style={{ width: `${pct}%` }} />
                    </span>
                  </button>
                </li>
              );
            })}
            {reading.length === 0 && (
              <li className="empty">Nothing yet. Search above to begin.</li>
            )}
          </ul>
        </section>
      )}
    </div>
  );
}
