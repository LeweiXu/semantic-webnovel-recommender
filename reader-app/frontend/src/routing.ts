export type AppRoute =
  | { page: "discover"; q: string; category: string | null; similar: string | null; title: string }
  | { page: "library"; q: string }
  | { page: "reader"; nid: string; chapter: number | null; line: number | null };

export const ROUTE_EVENT = "reader:navigate";

function nonNegativeInt(value: string | null): number | null {
  if (value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
}

export function currentRoute(): AppRoute {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  const params = new URLSearchParams(window.location.search);
  const readerMatch = path.match(/^\/reader\/([^/]+)$/);
  if (readerMatch) {
    return {
      page: "reader",
      nid: decodeURIComponent(readerMatch[1]),
      chapter: nonNegativeInt(params.get("chapter")),
      line: nonNegativeInt(params.get("line")),
    };
  }
  // Keep old shared links working and canonicalise them on the next update.
  const legacyNid = params.get("open");
  if (legacyNid) {
    return {
      page: "reader",
      nid: legacyNid,
      chapter: nonNegativeInt(params.get("ch")),
      line: nonNegativeInt(params.get("line")),
    };
  }
  if (path === "/library") return { page: "library", q: params.get("q") ?? "" };
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

export function libraryPath(q = "") {
  return withParams("/library", { q });
}

export function readerPath(nid: string, chapter?: number | null, line?: number | null) {
  return withParams(`/reader/${encodeURIComponent(nid)}`, { chapter, line });
}

export function navigate(path: string, replace = false) {
  window.history[replace ? "replaceState" : "pushState"]({}, "", path);
  window.dispatchEvent(new Event(ROUTE_EVENT));
}

// State is already updated by the caller; change only the address bar. This is
// used for search state and per-line reader position to avoid remounting.
export function writeUrl(path: string, replace = true) {
  window.history[replace ? "replaceState" : "pushState"]({}, "", path);
}
