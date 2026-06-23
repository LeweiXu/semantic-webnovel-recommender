import { useEffect } from "react";
import { useReader } from "./store/reader";
import { useSettings, applySettings } from "./store/settings";
import { LibraryPanel } from "./components/LibraryPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { TocPanel } from "./components/TocPanel";
import { ScrollReader } from "./components/ScrollReader";
import { DiscoverPage } from "./components/DiscoverPage";

export default function App() {
  const settings = useSettings();
  const novel = useReader((s) => s.novel);
  const loading = useReader((s) => s.loading);
  const error = useReader((s) => s.error);
  const furthest = useReader((s) => s.furthest);
  const view = useReader((s) => s.view);
  const setView = useReader((s) => s.setView);
  const leftOpen = useReader((s) => s.leftOpen);
  const rightOpen = useReader((s) => s.rightOpen);
  const tocOpen = useReader((s) => s.tocOpen);
  const toggleLeft = useReader((s) => s.toggleLeft);
  const toggleRight = useReader((s) => s.toggleRight);
  const toggleToc = useReader((s) => s.toggleToc);
  const openNovel = useReader((s) => s.openNovel);

  useEffect(() => applySettings(settings), [settings]);

  // Deep link: /?open=<nid>&ch=<index> opens a novel directly (shareable, and
  // lets the app restore to a specific novel on reload).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const nid = params.get("open");
    if (nid) {
      const ch = params.get("ch");
      openNovel(nid, ch ? Number(ch) : undefined);
    }
  }, [openNovel]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.tagName === "INPUT";
      if (e.key === "Escape") {
        toggleLeft(false);
        toggleRight(false);
        toggleToc(false);
      } else if (!typing && (e.key === "l" || e.key === "L")) {
        toggleLeft();
      } else if (!typing && (e.key === "s" || e.key === "S")) {
        toggleRight();
      } else if (!typing && (e.key === "c" || e.key === "C")) {
        toggleToc();
      } else if (!typing && (e.key === "d" || e.key === "D")) {
        setView("discover");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleLeft, toggleRight, toggleToc, setView]);

  const total = novel?.total ?? 0;
  const spinePct = total ? Math.min(100, ((furthest + 1) / total) * 100) : 0;

  return (
    <div className="app">
      {/* Progress gutter — the spine of the bound book, filling cinnabar as you read. */}
      <div className="spine" aria-hidden>
        <div className="spine-fill" style={{ height: `${spinePct}%` }} />
      </div>

      <header className="topbar">
        <div className="topbar-side">
          <button
            className={`icon-btn${view === "discover" ? " is-active" : ""}`}
            onClick={() => setView("discover")}
            aria-label="Discover (D)"
          >
            <CompassIcon />
          </button>
          <button className="icon-btn" onClick={() => toggleLeft()} aria-label="Library (L)">
            <span className="seal-glyph small" aria-hidden>读</span>
          </button>
          {novel && view === "read" && (
            <button
              className="icon-btn"
              onClick={() => toggleToc()}
              aria-label="Contents (C)"
            >
              <ContentsIcon />
            </button>
          )}
        </div>
        <div className="topbar-center">
          {novel && view === "read" && (
            <>
              <span className="topbar-title">{novel.title}</span>
              {total > 0 && (
                <span className="topbar-sub">
                  {Math.round(spinePct)}% · {furthest + 1}/{total}
                </span>
              )}
            </>
          )}
        </div>
        <div className="topbar-side topbar-side-right">
          <button className="icon-btn" onClick={() => toggleRight()} aria-label="Settings (S)">
            <GearIcon />
          </button>
        </div>
      </header>

      <main className="stage">
        {view === "discover" ? (
          <DiscoverPage />
        ) : loading ? (
          <div className="stage-note">Opening…</div>
        ) : error ? (
          <div className="stage-note">{error}</div>
        ) : novel ? (
          <ScrollReader key={novel.nid} />
        ) : (
          <DiscoverPage />
        )}
      </main>

      {/* Left drawer — library */}
      <div className={`scrim${leftOpen ? " is-open" : ""}`} onClick={() => toggleLeft(false)} />
      <aside className={`drawer left${leftOpen ? " is-open" : ""}`} aria-hidden={!leftOpen}>
        <LibraryPanel />
      </aside>

      {/* Left drawer — table of contents (mutually exclusive with the library) */}
      <div className={`scrim${tocOpen ? " is-open" : ""}`} onClick={() => toggleToc(false)} />
      <aside className={`drawer left${tocOpen ? " is-open" : ""}`} aria-hidden={!tocOpen}>
        <TocPanel />
      </aside>

      {/* Right drawer — settings */}
      <div className={`scrim${rightOpen ? " is-open" : ""}`} onClick={() => toggleRight(false)} />
      <aside className={`drawer right${rightOpen ? " is-open" : ""}`} aria-hidden={!rightOpen}>
        <SettingsPanel />
      </aside>
    </div>
  );
}

function CompassIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M15.5 8.5l-2 5-5 2 2-5 5-2Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
        fill="currentColor"
        fillOpacity="0.15"
      />
    </svg>
  );
}

function ContentsIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"
        stroke="currentColor"
        strokeWidth="1.4"
      />
      <path
        d="M19.4 13a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-2.87 1.2V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-2.87-1.2l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 13H4.5a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.2-2.87l-.06-.06A2 2 0 1 1 8.56 3.3l.06.06A1.7 1.7 0 0 0 11 4.6a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 2.87 1.2l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 11h.1a2 2 0 1 1 0 4h-.1Z"
        stroke="currentColor"
        strokeWidth="1.2"
      />
    </svg>
  );
}
