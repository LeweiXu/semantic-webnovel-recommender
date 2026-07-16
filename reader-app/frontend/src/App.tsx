import { useEffect, useState } from "react";
import { useReader } from "./store/reader";
import { useSettings, useActiveSettings, applySettings } from "./store/settings";
import { useSettingsSync } from "./hooks/useSettingsSync";
import { useIsMobile } from "./hooks/useIsMobile";
import { useAuth } from "./store/auth";
import { AdminPanel } from "./components/AdminPanel";
import { AuthPanel } from "./components/AuthPanel";
import { LibraryPage } from "./components/LibraryPage";
import { NovelPage } from "./components/NovelPage";
import { SettingsPanel } from "./components/SettingsPanel";
import { TocPanel } from "./components/TocPanel";
import { ScrollReader } from "./components/ScrollReader";
import { DiscoverPage } from "./components/DiscoverPage";
import { Footer } from "./components/Footer";
import {
  ROUTE_EVENT,
  currentRoute,
  discoverPath,
  libraryPath,
  navigate,
  novelPath,
  readerPath,
  writeUrl,
} from "./routing";

export default function App() {
  const activeSettings = useActiveSettings();
  const setProfile = useSettings((s) => s.setProfile);
  const isMobile = useIsMobile();
  const user = useAuth((s) => s.user);
  const restoreAuth = useAuth((s) => s.restore);
  const [authOpen, setAuthOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [routeKey, setRouteKey] = useState(() => `${window.location.pathname}${window.location.search}`);
  const novel = useReader((s) => s.novel);
  const loading = useReader((s) => s.loading);
  const error = useReader((s) => s.error);
  const furthest = useReader((s) => s.furthest);
  const view = useReader((s) => s.view);
  const setView = useReader((s) => s.setView);
  const rightOpen = useReader((s) => s.rightOpen);
  const tocOpen = useReader((s) => s.tocOpen);
  const toggleRight = useReader((s) => s.toggleRight);
  const toggleToc = useReader((s) => s.toggleToc);
  const openNovel = useReader((s) => s.openNovel);
  const chromeVisible = useReader((s) => s.chromeVisible);

  // The viewport decides which settings profile is active (desktop vs mobile).
  useEffect(() => setProfile(isMobile ? "mobile" : "desktop"), [isMobile, setProfile]);
  useEffect(() => applySettings(activeSettings), [activeSettings]);
  useEffect(() => { restoreAuth(); }, [restoreAuth]);
  useSettingsSync();

  useEffect(() => {
    const syncRoute = () => {
      const route = currentRoute();
      if (route.page === "reader") {
        // The URL carries only the chapter (line lives in the server bookmark).
        // Passing no line lets openNovel restore the exact saved line when this
        // chapter is the bookmarked one, and land on the chapter top otherwise.
        const location = route.chapter === null
          ? undefined
          : { chapter: route.chapter };
        openNovel(route.nid, location, route.chapter === null ? "replace" : false);
        const canonical = readerPath(route.nid, route.chapter);
        if (`${window.location.pathname}${window.location.search}` !== canonical) writeUrl(canonical);
      } else if (route.page === "novel") {
        setView("novel");
        const canonical = novelPath(route.nid);
        if (window.location.pathname !== canonical) writeUrl(canonical);
      } else if (route.page === "library") {
        setView("library");
      } else {
        setView("discover");
        if (window.location.pathname !== "/discover") writeUrl(discoverPath(route));
      }
      setRouteKey(`${window.location.pathname}${window.location.search}`);
    };
    window.addEventListener("popstate", syncRoute);
    window.addEventListener(ROUTE_EVENT, syncRoute);
    syncRoute();
    return () => {
      window.removeEventListener("popstate", syncRoute);
      window.removeEventListener(ROUTE_EVENT, syncRoute);
    };
  }, [openNovel, setView]);

  const total = novel?.total ?? 0;
  const spinePct = total ? Math.min(100, ((furthest + 1) / total) * 100) : 0;
  // The stripped-down header (Contents + Settings only) applies to the reader on
  // mobile/tablet. The novel page keeps the full header so there's a way out.
  const mobileReader = isMobile && view === "read";

  return (
    <div className={`app${view === "read" && !chromeVisible ? " chrome-hidden" : ""}`}>
      {/* Progress gutter — the spine of the bound book, filling cinnabar as you read. */}
      <div className="spine" aria-hidden>
        <div className="spine-fill" style={{ height: `${spinePct}%` }} />
      </div>

      <header className="topbar">
        <div className="topbar-side">
          {/* In the mobile reader, the left cluster is just Contents. Everywhere
              else (and on desktop) show the full navigation. */}
          {!mobileReader && (
            <>
              <button
                className={`icon-btn${view === "library" ? " is-active" : ""}`}
                onClick={() => navigate(libraryPath())}
                aria-label="Library"
              >
                <span className="seal-glyph small" aria-hidden>读</span>
              </button>
              <button
                className={`icon-btn${view === "discover" ? " is-active" : ""}`}
                onClick={() => navigate(discoverPath())}
                aria-label="Discover"
              >
                <CompassIcon />
              </button>
            </>
          )}
          {novel && view === "read" && (
            <button
              className="icon-btn"
              onClick={() => toggleToc()}
              aria-label="Contents"
            >
              <ContentsIcon />
            </button>
          )}
        </div>
        <div className="topbar-center">
          {novel && view === "read" && (
            <>
              {/* Tapping the title opens the novel page — the exit route when the
                  mobile reader header hides the rest of the navigation. */}
              <button
                className="topbar-title"
                onClick={() => navigate(novelPath(novel.nid))}
                aria-label="Novel page"
              >
                {novel.title}
              </button>
              {total > 0 && !mobileReader && (
                <span className="topbar-sub">
                  {Math.round(spinePct)}% · {furthest + 1}/{total}
                </span>
              )}
            </>
          )}
        </div>
        <div className="topbar-side topbar-side-right">
          {/* In the mobile reader, the right cluster is just Settings. */}
          {!mobileReader && user?.username === "lingwei" && (
            <button className="icon-btn" onClick={() => setAdminOpen(true)} aria-label="Admin jobs">
              <span className="account-glyph" aria-hidden>管</span>
            </button>
          )}
          {!mobileReader && (
            <button className="account-btn" onClick={() => setAuthOpen(true)} aria-label="Account">
              {user?.username ?? "Log in"}
            </button>
          )}
          <button className="icon-btn" onClick={() => toggleRight()} aria-label="Settings">
            <GearIcon />
          </button>
        </div>
      </header>

      <main className="stage">
        {view === "discover" ? (
          <DiscoverPage key={routeKey} />
        ) : view === "library" ? (
          <LibraryPage key={routeKey} />
        ) : view === "novel" ? (
          <NovelPage key={routeKey} />
        ) : loading ? (
          <div className="stage-note">Opening…</div>
        ) : error ? (
          <div className="stage-note">{error}</div>
        ) : novel ? (
          <ScrollReader key={novel.nid} />
        ) : (
          <DiscoverPage key={routeKey} />
        )}
      </main>

      {/* Left drawer — table of contents */}
      <div className={`scrim${tocOpen ? " is-open" : ""}`} onClick={() => toggleToc(false)} />
      <aside className={`drawer left${tocOpen ? " is-open" : ""}`} aria-hidden={!tocOpen}>
        <TocPanel />
      </aside>

      {/* Right drawer — settings */}
      <div className={`scrim${rightOpen ? " is-open" : ""}`} onClick={() => toggleRight(false)} />
      <aside className={`drawer right${rightOpen ? " is-open" : ""}`} aria-hidden={!rightOpen}>
        <SettingsPanel />
      </aside>

      <Footer />

      {authOpen && <AuthPanel onClose={() => setAuthOpen(false)} />}
      {adminOpen && user?.username === "lingwei" && (
        <AdminPanel onClose={() => setAdminOpen(false)} />
      )}
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
