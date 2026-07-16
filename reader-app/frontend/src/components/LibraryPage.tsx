import { useEffect, useMemo, useState } from "react";
import { api, type ReadingItem, type SearchItem } from "../api/client";
import { useAuth } from "../store/auth";
import { DownloadDialog } from "./DownloadDialog";
import { currentRoute, libraryPath, navigate, novelPath, writeUrl } from "../routing";

const isNovelUrl = (s: string) =>
  /^https?:\/\/(www\.)?52shuku\.net\/[^/]+\/.+\.html$/i.test(s.trim());

const CAT_LABEL: Record<string, string> = { gl: "百合", yanqing: "言情" };
const catLabel = (c: string) => CAT_LABEL[c] ?? c;

function pct(position: number, total: number | null): number {
  if (!total) return 0;
  return Math.min(100, Math.round(((position + 1) / total) * 100));
}

export function LibraryPage() {
  const user = useAuth((s) => s.user);

  const [reading, setReading] = useState<ReadingItem[]>([]);
  const route = currentRoute();
  const [query, setQuery] = useState(route.page === "library" ? route.q : "");
  const [results, setResults] = useState<SearchItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [downloadResult, setDownloadResult] = useState<SearchItem | null>(null);

  const refreshReading = () => {
    if (!user) {
      setReading([]);
      return;
    }
    api.reading().then(setReading).catch(() => {});
  };
  useEffect(refreshReading, [user]);

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
    <div className="library">
      <header className="lib-hero">
        <span className="seal-glyph small" aria-hidden>读</span>
        <h1 className="lib-title">Library</h1>
        <p className="lib-sub">
          Your shelf of novels in progress. Search the catalogue, or paste a
          52shuku link to download something new.
        </p>
        <div className="lib-search-wrap">
          <input
            className="lib-search"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              writeUrl(libraryPath(e.target.value));
            }}
            placeholder="Search titles, or paste a 52shuku link…"
            spellCheck={false}
            autoComplete="off"
          />
        </div>
      </header>

      {pastedUrl && user && (
        <div className="lib-panel">
          <DownloadDialog
            url={pastedUrl}
            onDone={(nid) => {
              setQuery("");
              writeUrl(libraryPath());
              refreshReading();
              navigate(novelPath(nid));
            }}
          />
        </div>
      )}
      {pastedUrl && !user && (
        <div className="lib-panel">
          <div className="login-note">Log in from the account button to download this novel.</div>
        </div>
      )}

      {!pastedUrl && query.trim() && (
        <section className="lib-section">
          <div className="dsc-section-label">{searching ? "Searching…" : "Results"}</div>
          <ul className="lib-results">
            {results.map((r) => (
              <li key={r.nid} className="lib-result">
                <button
                  className="lib-result-main"
                  disabled={!r.downloaded}
                  onClick={() => r.downloaded && navigate(novelPath(r.nid))}
                  title={r.downloaded ? "" : "Not downloaded yet"}
                >
                  <span className="lib-result-title">{r.title}</span>
                  <span className="lib-result-meta">
                    {r.author || "—"}
                    {r.category ? ` · ${catLabel(r.category)}` : ""}
                    {!r.downloaded && <em className="tag-meta"> · metadata only</em>}
                  </span>
                </button>
                {!r.downloaded && (
                  <button
                    className="result-download"
                    disabled={!user}
                    title={user ? "Download this novel" : "Log in to download"}
                    onClick={() => setDownloadResult(r)}
                  >
                    Download
                  </button>
                )}
              </li>
            ))}
            {!searching && results.length === 0 && <li className="empty">No matches.</li>}
          </ul>
          {downloadResult && (
            <DownloadDialog
              url={downloadResult.url}
              onDone={(nid) => {
                setDownloadResult(null);
                setQuery("");
                writeUrl(libraryPath());
                refreshReading();
                navigate(novelPath(nid));
              }}
            />
          )}
        </section>
      )}

      {!query.trim() && (
        <section className="lib-section">
          <div className="dsc-section-label">Currently reading</div>
          {reading.length === 0 ? (
            <div className="dsc-note dsc-idle">
              {user
                ? "Nothing yet. Search above, or find something on Discover."
                : "Log in from the account button to keep a reading shelf."}
            </div>
          ) : (
            <div className="lib-grid">
              {reading.map((r) => (
                <button
                  key={r.nid}
                  className="lib-card"
                  onClick={() => navigate(novelPath(r.nid))}
                >
                  <div className="lib-card-head">
                    <h3 className="lib-card-title">{r.title}</h3>
                    <span className="lib-card-author">
                      {r.author || "—"}
                      {r.category ? ` · ${catLabel(r.category)}` : ""}
                    </span>
                  </div>
                  {r.synopsis && <p className="lib-card-synopsis">{r.synopsis}</p>}
                  {(r.tags ?? []).length > 0 && (
                    <div className="lib-card-tags">
                      {(r.tags ?? []).slice(0, 4).map((t) => (
                        <span key={t} className="lib-tag">{t}</span>
                      ))}
                    </div>
                  )}
                  <div className="lib-card-foot">
                    <span className="lib-bar" aria-hidden>
                      <span className="lib-bar-fill" style={{ width: `${pct(r.position, r.total)}%` }} />
                    </span>
                    <span className="lib-card-meta">
                      ch {r.position + 1}{r.total ? `/${r.total}` : ""} · {pct(r.position, r.total)}%
                    </span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
