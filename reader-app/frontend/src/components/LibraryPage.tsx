import { useEffect, useMemo, useState } from "react";
import { api, type ShelfItem, type SearchItem } from "../api/client";
import { useAuth } from "../store/auth";
import { DownloadDialog } from "./DownloadDialog";
import { FileBrowser } from "./FileBrowser";
import { currentRoute, libraryPath, navigate, novelPath, writeUrl } from "../routing";

const isNovelUrl = (s: string) =>
  /^https?:\/\/(www\.)?52shuku\.net\/[^/]+\/.+\.html$/i.test(s.trim());

const CAT_LABEL: Record<string, string> = { gl: "百合", yanqing: "言情" };
const catLabel = (c: string) => CAT_LABEL[c] ?? c;

// The shelf takes ~1s to come back from the backend, so cache the last result
// per user in localStorage and show it instantly, then refresh in the
// background (stale-while-revalidate).
const SHELF_CACHE_PREFIX = "reader:library:shelf:";

function readShelfCache(username: string): ShelfItem[] {
  try {
    const raw = localStorage.getItem(SHELF_CACHE_PREFIX + username);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeShelfCache(username: string, items: ShelfItem[]) {
  try {
    localStorage.setItem(SHELF_CACHE_PREFIX + username, JSON.stringify(items));
  } catch {
    /* quota or private mode — the network fetch still works, just no cache */
  }
}

function pct(position: number, total: number | null): number {
  if (!total) return 0;
  return Math.min(100, Math.round(((position + 1) / total) * 100));
}

// Fetch a downloadable file and save it through a temporary anchor. The blob
// carries the auth header (a plain <a href> can't), so downloads stay logged-in.
async function saveDoc(item: { id: string; title: string }) {
  const blob = await api.downloadFile(item.id);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = item.id.split("/").pop() || item.title;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function LibraryPage() {
  const user = useAuth((s) => s.user);
  const ready = useAuth((s) => s.ready);

  const [shelf, setShelf] = useState<ShelfItem[]>(() =>
    user ? readShelfCache(user.username) : [],
  );
  const route = currentRoute();
  const initialBrowsePath = route.page === "library" ? route.path : "";
  const [query, setQuery] = useState(route.page === "library" ? route.q : "");
  const [results, setResults] = useState<SearchItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [downloadResult, setDownloadResult] = useState<SearchItem | null>(null);
  const [browseOpen, setBrowseOpen] = useState(!!initialBrowsePath);

  const refreshShelf = () => {
    if (!user) {
      setShelf([]);
      return;
    }
    // Paint the cached shelf immediately, then reconcile with the server.
    setShelf(readShelfCache(user.username));
    api
      .shelf()
      .then((items) => {
        setShelf(items);
        writeShelfCache(user.username, items);
      })
      .catch(() => {});
  };
  useEffect(refreshShelf, [user]);

  const removeItem = (item: ShelfItem) => {
    setShelf((cur) => cur.filter((it) => it.id !== item.id)); // optimistic
    api
      .removeFromShelf(item.id)
      .then((items) => {
        setShelf(items);
        if (user) writeShelfCache(user.username, items);
      })
      .catch(refreshShelf);
  };

  const openItem = (item: ShelfItem) => {
    if (item.kind === "doc") saveDoc(item).catch(() => {});
    else navigate(novelPath(item.id));
  };

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
          Your shelf of novels. Search the catalogue, browse your files, or paste
          a 52shuku link to download something new.
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
              refreshShelf();
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
                  onClick={() => r.downloaded && navigate(novelPath(r.slug ?? r.nid))}
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
                refreshShelf();
                navigate(novelPath(nid));
              }}
            />
          )}
        </section>
      )}

      {!query.trim() && (
        <section className="lib-section">
          <button
            className="fb-toggle"
            onClick={() => setBrowseOpen((v) => !v)}
            aria-expanded={browseOpen}
          >
            <span className={`fb-caret${browseOpen ? " is-open" : ""}`} aria-hidden>▸</span>
            Browse files
          </button>
          {browseOpen && <FileBrowser initialPath={initialBrowsePath} onChange={refreshShelf} />}
        </section>
      )}

      {!query.trim() && (
        <section className="lib-section">
          <div className="dsc-section-label">Your library</div>
          {shelf.length === 0 ? (
            <div className="dsc-note dsc-idle">
              {!ready
                ? " " /* auth still restoring — don't flash the logged-out prompt */
                : user
                ? "Nothing yet. Browse your files above, search, or find something on Discover."
                : "Log in from the account button to keep a library."}
            </div>
          ) : (
            <div className="lib-grid">
              {shelf.map((r) => (
                <div key={r.id} className="lib-card-wrap">
                  <button
                    className="lib-card"
                    onClick={() => openItem(r)}
                  >
                    <div className="lib-card-head">
                      <h3 className="lib-card-title">{r.title}</h3>
                      <span className="lib-card-author">
                        {r.author || (r.kind === "doc" ? "Document" : "—")}
                        {r.category ? ` · ${catLabel(r.category)}` : ""}
                        {r.language === "en" ? " · EN" : ""}
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
                      {r.kind === "doc" ? (
                        <span className="lib-card-meta">Download file</span>
                      ) : (
                        <>
                          <span className="lib-bar" aria-hidden>
                            <span className="lib-bar-fill" style={{ width: `${pct(r.position, r.total)}%` }} />
                          </span>
                          <span className="lib-card-meta">
                            ch {r.position + 1}{r.total ? `/${r.total}` : ""} · {pct(r.position, r.total)}%
                          </span>
                        </>
                      )}
                    </div>
                  </button>
                  <button
                    className="lib-card-remove"
                    onClick={() => removeItem(r)}
                    aria-label="Remove from library"
                    title="Remove from library"
                  >
                    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                      <path
                        d="M2.5 2.5l7 7M9.5 2.5l-7 7"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                      />
                    </svg>
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
