export type AppRoute =
  | { page: "discover"; q: string; category: string | null; similar: string | null; title: string }
  | { page: "library"; q: string; path: string }
  | { page: "novel"; id: string }
  | { page: "reader"; id: string; chapter: number | null; line: number | null };

export const ROUTE_EVENT = "reader:navigate";

// The address bar counts chapters from 1; the app counts from 0.
function readerChapter(value: string | null): number | null {
  const shown = nonNegativeInt(value);
  return shown === null ? null : Math.max(0, shown - 1);
}

function nonNegativeInt(value: string | null): number | null {
  if (value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
}

// A novel id is a readable "<category>/<stem>" slug (or a legacy base64 id).
// Encode each path segment but keep the slash so the URL stays a real path.
function encodeSlug(id: string): string {
  return id.split("/").map(encodeURIComponent).join("/");
}
function decodeSlug(encoded: string): string {
  return encoded.split("/").map(decodeURIComponent).join("/");
}

export function currentRoute(): AppRoute {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  const params = new URLSearchParams(window.location.search);
  const novelMatch = path.match(/^\/novel\/(.+)$/);
  if (novelMatch) {
    return { page: "novel", id: decodeSlug(novelMatch[1]) };
  }
  const readerMatch = path.match(/^\/reader\/(.+)$/);
  if (readerMatch) {
    return {
      page: "reader",
      id: decodeSlug(readerMatch[1]),
      chapter: readerChapter(params.get("chapter")),
      line: nonNegativeInt(params.get("line")),
    };
  }
  // Keep old shared links working and canonicalise them on the next update.
  const legacyNid = params.get("open");
  if (legacyNid) {
    return {
      page: "reader",
      id: legacyNid,
      chapter: nonNegativeInt(params.get("ch")),
      line: nonNegativeInt(params.get("line")),
    };
  }
  if (path === "/library") {
    return { page: "library", q: params.get("q") ?? "", path: params.get("path") ?? "" };
  }
  return {
    page: "discover",
    q: params.get("q") ?? "",
    category: params.get("category"),
    similar: params.get("similar"),
    title: params.get("title") ?? "",
  };
}

function withParams(path: string, entries: Record<string, string | number | null | undefined>) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(entries)) {
    if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

export function discoverPath(options: {
  q?: string;
  category?: string | null;
  similar?: string | null;
  title?: string;
} = {}) {
  return withParams("/discover", options);
}

// Search (`q`) and the file-explorer folder (`path`) share the /library URL, but
// the browser only shows when the search box is empty, so they never both matter.
export function libraryPath(q = "", path = "") {
  return withParams("/library", { q, path });
}

export function novelPath(id: string) {
  return `/novel/${encodeSlug(id)}`;
}

// The reader URL carries only the chapter. Intra-chapter line progress is kept
// server-side (the reading bookmark), not exposed in the address bar.
//
// Chapters are 0-based everywhere inside the app but 1-based in the address bar,
// so `?chapter=` matches the number the reader prints above the text. The
// conversion lives here and in currentRoute() so nothing else has to think
// about it.
export function readerPath(id: string, chapter?: number | null) {
  const shown = chapter === null || chapter === undefined ? chapter : chapter + 1;
  return withParams(`/reader/${encodeSlug(id)}`, { chapter: shown });
}

export function navigate(path: string, replace = false) {
  window.history[replace ? "replaceState" : "pushState"]({}, "", path);
  window.dispatchEvent(new Event(ROUTE_EVENT));
}

// Structural, so routing.ts stays framework-free: React's synthetic mouse event
// satisfies this shape.
interface PlainMouseEvent {
  button: number;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  defaultPrevented: boolean;
  preventDefault: () => void;
}

// Props for a navigation control that is a real link. Rendering an <a href>
// means middle-click, ctrl/cmd-click and "open in new tab" behave as they do on
// any other site; a plain left-click is still routed client-side, with no reload.
export function linkProps(path: string): {
  href: string;
  onClick: (event: PlainMouseEvent) => void;
} {
  return {
    href: path,
    onClick: (event) => {
      if (event.defaultPrevented) return;
      // Let the browser handle anything that means "open this somewhere else".
      if (event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate(path);
    },
  };
}

// State is already updated by the caller; change only the address bar. This is
// used for search state and per-line reader position to avoid remounting.
//
// Callers on a scroll path (the reader) hit this every animation frame, so skip
// replaceState when the URL wouldn't actually change. Firefox mobile treats each
// history write as a location change: it re-runs reader-view detection and resets
// the dynamic toolbar's auto-hide, so a per-frame no-op write pins the URL bar
// open and makes the reader-view icon flicker. Only `replace` is guarded, since
// a repeat pushState is a deliberate history entry.
export function writeUrl(path: string, replace = true) {
  if (replace && `${window.location.pathname}${window.location.search}` === path) return;
  window.history[replace ? "replaceState" : "pushState"]({}, "", path);
}
