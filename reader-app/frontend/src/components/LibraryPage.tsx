import { useEffect, useMemo, useState } from "react";
import { api, type ShelfItem, type SearchItem } from "../api/client";
import { useAuth } from "../store/auth";
import { useDownloads, type DlState } from "../store/downloads";
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

// A card for a shelf novel whose file isn't here yet. Shows live page progress
// while downloading, an error with dismiss on failure, or a Download/retry
// button when there's no active download (e.g. after a reload).
function DownloadingCard({
  item,
  title,
  dl,
  onRetry,
  onDismiss,
}: {
  item?: ShelfItem; // full metadata for a shelf-backed download (author, tags, synopsis)
  title?: string; // fallback name when there's no shelf item yet
  dl?: DlState;
  onRetry?: () => void;
  onDismiss?: () => void;
}) {
  const running = dl?.status === "queued" || dl?.status === "running";
  const errored = dl?.status === "error";
  const pctDone = dl && dl.total > 0 ? Math.round((dl.done / dl.total) * 100) : 0;
  const name = item?.title ?? title ?? dl?.title ?? "";
  const tags = item?.tags ?? [];
  return (
    <div className="lib-card-wrap">
      <div className={`lib-card is-downloading${errored ? " is-error" : ""}`}>
        <div className="lib-card-head">
          <h3 className="lib-card-title">{name}</h3>
          {item && (item.author || item.category) && (
            <span className="lib-card-author">
              {item.author || "—"}
              {item.category ? ` · ${catLabel(item.category)}` : ""}
            </span>
          )}
        </div>
        {item?.synopsis && <p className="lib-card-synopsis">{item.synopsis}</p>}
        {tags.length > 0 && (
          <div className="lib-card-tags">
            {tags.slice(0, 4).map((t) => (
              <span key={t} className="lib-tag">{t}</span>
            ))}
          </div>
        )}
        {running ? (
          <div className="lib-card-foot">
            <span className="lib-bar" aria-hidden>
              <span className="lib-bar-fill" style={{ width: `${pctDone}%` }} />
            </span>
            <span className="lib-card-meta">
              {dl?.total ? `Downloading… ${dl.done}/${dl.total}` : "Queued…"}
            </span>
          </div>
        ) : (
          <div className="lib-card-foot lib-card-dl-idle">
            <span className="lib-card-meta">{errored ? dl?.error : "Tap to download this novel."}</span>
            {onRetry && (
              <button className="result-download" onClick={onRetry}>
                {errored ? "Retry" : "Download"}
              </button>
            )}
          </div>
        )}
      </div>
      {!running && onDismiss && (
        <button className="lib-card-remove" onClick={onDismiss} aria-label="Remove" title="Remove">
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
            <path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      )}
    </div>
  );
}

export function LibraryPage() {
  const user = useAuth((s) => s.user);
  const ready = useAuth((s) => s.ready);
  const downloads = useDownloads((s) => s.map);
  const startDownload = useDownloads((s) => s.start);
  const clearDownload = useDownloads((s) => s.clear);

  const [shelf, setShelf] = useState<ShelfItem[]>(() =>
    user ? readShelfCache(user.username) : [],
  );
  const route = currentRoute();
  const initialBrowsePath = route.page === "library" ? route.path : "";
  const [query, setQuery] = useState(route.page === "library" ? route.q : "");
  const [results, setResults] = useState<SearchItem[]>([]);
  const [searching, setSearching] = useState(false);
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

  // Re-fetch the shelf whenever a download changes phase (a novel is added on
  // start, and flips to downloaded when it finishes). Keyed on statuses only, so
  // per-page progress ticks don't spam the network.
  const dlSignature = Object.values(downloads)
    .map((d) => `${d.url}:${d.status}`)
    .sort()
    .join("|");
  useEffect(() => {
    if (user) refreshShelf();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dlSignature]);

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

  // Downloads with no shelf card yet (the brief gap before the first refresh).
  const shelfUrls = new Set(shelf.map((s) => s.url));
  const orphanDownloads = Object.values(downloads).filter(
    (d) => d.status !== "done" && !shelfUrls.has(d.url),
  );

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
            {results.map((r) => {
              const dl = downloads[r.url];
              const running = dl?.status === "queued" || dl?.status === "running";
              const downloaded = r.downloaded || dl?.status === "done";
              return (
                <li key={r.nid} className={`lib-result${!downloaded && !running ? " is-faded" : ""}`}>
                  <button
                    className="lib-result-main"
                    disabled={!downloaded}
                    onClick={() => downloaded && navigate(novelPath(r.slug ?? dl?.slug ?? r.nid))}
                    title={downloaded ? "" : "Not downloaded yet"}
                  >
                    <span className="lib-result-title">{r.title}</span>
                    <span className="lib-result-meta">
                      {r.author || "—"}
                      {r.category ? ` · ${catLabel(r.category)}` : ""}
                      {running && (
                        <em className="tag-meta">
                          {" · "}
                          {dl?.total ? `downloading ${dl.done}/${dl.total}` : "downloading…"}
                        </em>
                      )}
                      {!downloaded && !running && <em className="tag-meta"> · metadata only</em>}
                    </span>
                  </button>
                  {!downloaded && (
                    <button
                      className="result-download"
                      disabled={!user || running}
                      title={user ? "Download this novel" : "Log in to download"}
                      onClick={() => startDownload(r.url)}
                    >
                      {running ? "Downloading…" : "Download"}
                    </button>
                  )}
                </li>
              );
            })}
            {!searching && results.length === 0 && <li className="empty">No matches.</li>}
          </ul>
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
          {shelf.length === 0 && orphanDownloads.length === 0 ? (
            <div className="dsc-note dsc-idle">
              {!ready
                ? " " /* auth still restoring — don't flash the logged-out prompt */
                : user
                ? "Nothing yet. Browse your files above, search, or find something on Discover."
                : "Log in from the account button to keep a library."}
            </div>
          ) : (
            <div className="lib-grid">
              {orphanDownloads.map((d) => (
                <DownloadingCard
                  key={d.url}
                  title={d.title}
                  dl={d}
                  onRetry={() => startDownload(d.url)}
                  onDismiss={() => clearDownload(d.url)}
                />
              ))}
              {shelf.map((r) => {
                const dl = downloads[r.url];
                if (!r.downloaded) {
                  return (
                    <DownloadingCard
                      key={r.id}
                      item={r}
                      dl={dl}
                      onRetry={() => startDownload(r.url)}
                      onDismiss={() => {
                        clearDownload(r.url);
                        removeItem(r);
                      }}
                    />
                  );
                }
                return (
                  <div key={r.id} className="lib-card-wrap">
                    <button className="lib-card" onClick={() => openItem(r)}>
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
                        <path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                      </svg>
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
