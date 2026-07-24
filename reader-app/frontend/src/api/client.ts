// Typed access to the reader backend. Same-origin remains supported, while a
// deployed frontend can point at the API with VITE_API_BASE.

export const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
let authToken: string | null = null;

export function setApiAuthToken(token: string | null) {
  authToken = token;
}

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

// A novel id may be a readable "<category>/<stem>" slug (with Han characters and
// one slash) or a legacy base64 id. Encode each path segment but keep the slash.
function encodeId(id: string): string {
  return id.split("/").map(encodeURIComponent).join("/");
}

function authHeaders(headers?: HeadersInit): Headers {
  const result = new Headers(headers);
  if (authToken) result.set("Authorization", `Bearer ${authToken}`);
  return result;
}

async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(apiUrl(path), { ...init, headers: authHeaders(init.headers) });
}

export interface ReadingItem {
  url: string;
  nid: string;
  slug: string | null;
  title: string;
  author: string;
  category: string;
  position: number;
  line: number | null;
  total: number | null;
  updated: string;
  tags: string[];
  synopsis: string;
}

export interface ShelfItem {
  id: string;
  title: string;
  author: string;
  category: string;
  kind: string; // "novel" | "text" | "doc"
  language: string;
  downloaded: boolean; // false while a novel is still downloading
  url: string; // download/progress key, matches the downloads store
  position: number;
  total: number | null;
  updated: string;
  added: string;
  tags: string[];
  synopsis: string;
}

export interface SearchItem {
  url: string;
  nid: string;
  slug: string | null;
  title: string;
  author: string;
  category: string;
  downloaded: boolean;
  chapter_count: number | null;
}

export interface ChapterStub {
  index: number;
  title: string;
}

export interface NovelDetail {
  url: string;
  nid: string;
  slug: string;
  title: string;
  author: string;
  category: string;
  tags: string[];
  synopsis: string;
  synopsis_tokens: Token[];
  downloaded: boolean;
  total: number;
  position: number;
  line: number | null;
  anchor_version: number;
  chapters: ChapterStub[];
  kind: string; // "novel" | "text"
  language: string; // "zh" | "en"
  chapter_mode: "detected" | "fallback" | "custom";
  chapter_pattern: string | null;
}

export interface ChapterPatternResult {
  pattern: string;
  matches: number;
  chapters: number;
  examples: string[];
  selected_chapter: number;
}

export type BrowseKind = "dir" | "text" | "doc" | "other";

export interface BrowseEntry {
  name: string;
  path: string;
  kind: BrowseKind;
  size: number | null;
}

export interface BrowseListing {
  path: string;
  parent: string | null;
  entries: BrowseEntry[];
}

export interface DownloadDTO {
  url: string;
  nid: string;
  title: string;
  status: "queued" | "running" | "done" | "error";
  done: number;
  total: number;
  slug: string | null;
  error: string | null;
}

export interface Token {
  t: string;
  py: string | null;
}

export interface ChapterContent {
  index: number;
  title: string;
  total: number;
  tokens: Token[];
  prev: number | null;
  next: number | null;
}

export interface RecItem {
  nid: string;
  slug: string | null;
  url: string;
  title: string;
  author: string;
  category: string;
  status: string;
  tags: string[];
  synopsis: string;
  cosine: number;
  similarity: number; // 0–100, for the score bar
  downloaded: boolean;
}

export interface SimilarOut {
  seed: { nid: string; title: string; category: string } | null;
  results: RecItem[];
}

export interface MapPoint {
  nid: string;
  x: number; // PCA axis 1, scaled to [-1, 1]
  y: number; // PCA axis 2, scaled to [-1, 1]
  category: string;
  title: string;
  tags: string[];
  downloaded: boolean;
}

export interface MapData {
  points: MapPoint[];
  categories: string[];
  count: number;
}

export interface TagCount {
  tag: string;
  count: number;
}

export interface DefineEntry {
  pinyin: string;
  defs: string[];
}

export interface PerChar {
  char: string;
  pinyin: string;
  defs: string[];
}

export interface DefineOut {
  word: string;
  entries: DefineEntry[];
  perChar: PerChar[];
}

export interface User {
  username: string;
  created: string;
}

export interface TokenOut {
  access_token: string;
  token_type: string;
  user: User;
}

export interface AdminJob {
  id: string;
  script: string;
  args: string[];
  pid: number;
  status: string;
  started: string;
  logfile: string;
  returncode: number | null;
}

export interface AdminCommand {
  id: string;
  command: string;
}

export interface GlobalChapterPattern {
  id: string;
  label: string;
  pattern: string;
  builtin: boolean;
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await apiFetch(url);
  if (!res.ok) throw await responseError(res);
  return res.json() as Promise<T>;
}

async function responseError(res: Response): Promise<Error> {
  try {
    const body = await res.json();
    return new Error(body.detail || `${res.status} ${res.statusText}`);
  } catch {
    return new Error(`${res.status} ${res.statusText}`);
  }
}

async function postJSON<T>(url: string, body?: unknown, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  const res = await apiFetch(url, {
    ...init,
    method: "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await responseError(res);
  return res.json() as Promise<T>;
}

async function putJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await apiFetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await responseError(res);
  return res.json() as Promise<T>;
}

async function deleteJSON<T>(url: string): Promise<T> {
  const res = await apiFetch(url, { method: "DELETE" });
  if (!res.ok) throw await responseError(res);
  return res.json() as Promise<T>;
}

export const api = {
  reading: () => getJSON<ReadingItem[]>("/api/library/reading"),
  search: (q: string) =>
    getJSON<SearchItem[]>(`/api/library/search?q=${encodeURIComponent(q)}`),
  browse: (path = "") =>
    getJSON<BrowseListing>(`/api/browse?path=${encodeURIComponent(path)}`),
  startDownload: (url: string) => postJSON<DownloadDTO>("/api/download", { url }),
  downloads: () => getJSON<DownloadDTO[]>("/api/downloads"),
  shelf: () => getJSON<ShelfItem[]>("/api/library/shelf"),
  addToShelf: (id: string) => postJSON<ShelfItem[]>("/api/library/shelf", { id }),
  removeFromShelf: (id: string) =>
    deleteJSON<ShelfItem[]>(`/api/library/shelf?id=${encodeURIComponent(id)}`),
  // Fetch a downloadable file (epub/pdf/docx) as a blob, auth header attached.
  downloadFile: async (path: string): Promise<Blob> => {
    const res = await apiFetch(`/api/file/download?path=${encodeURIComponent(path)}`);
    if (!res.ok) throw await responseError(res);
    return res.blob();
  },
  novel: (nid: string) => getJSON<NovelDetail>(`/api/novel/${encodeId(nid)}`),
  chapter: (nid: string, idx: number, annotate: boolean) =>
    getJSON<ChapterContent>(
      `/api/novel/${encodeId(nid)}/chapter/${idx}?annotate=${annotate ? 1 : 0}`,
    ),
  previewChapterPattern: (nid: string, sample: string, pattern = "") =>
    postJSON<ChapterPatternResult>(
      `/api/novel/${encodeId(nid)}/chapter-pattern/preview`,
      { sample, pattern },
    ),
  saveChapterPattern: (nid: string, pattern: string, sample = "") =>
    putJSON<ChapterPatternResult>(
      `/api/novel/${encodeId(nid)}/chapter-pattern`,
      { pattern, sample },
    ),
  deleteChapterPattern: (nid: string) =>
    deleteJSON<ChapterPatternResult>(
      `/api/novel/${encodeId(nid)}/chapter-pattern`,
    ),
  define: (word: string) =>
    getJSON<DefineOut>(`/api/define?word=${encodeURIComponent(word)}`),
  recommend: (q: string, n = 12, category?: string) =>
    getJSON<{ results: RecItem[]; error?: string }>(
      `/api/recommend?q=${encodeURIComponent(q)}&n=${n}` +
        (category ? `&category=${encodeURIComponent(category)}` : ""),
    ),
  similar: (nid: string, n = 12, category?: string) =>
    getJSON<SimilarOut>(
      `/api/similar/${nid}?n=${n}` +
        (category ? `&category=${encodeURIComponent(category)}` : ""),
    ),
  discoverMap: () => getJSON<MapData>(`/api/discover/map`),
  discoverTags: (limit = 18) => getJSON<TagCount[]>(`/api/discover/tags?limit=${limit}`),
  setProgress: (nid: string, position: number, line: number | null, reset = false, keepalive = false) =>
    postJSON<{
      ok: boolean;
      position: number;
      line: number | null;
      anchor_version: number;
      updated: string;
    }>(
      `/api/novel/${encodeId(nid)}/progress`,
      { position, line, anchor_version: 2, reset },
      { keepalive },
    ),
  register: (username: string, password: string) =>
    postJSON<TokenOut>("/api/auth/register", { username, password }),
  login: (username: string, password: string) =>
    postJSON<TokenOut>("/api/auth/login", { username, password }),
  me: () => getJSON<User>("/api/auth/me"),
  getSettings: () => getJSON<Record<string, unknown>>("/api/settings"),
  putSettings: (settings: Record<string, unknown>) =>
    putJSON<Record<string, unknown>>("/api/settings", settings),
  adminJobs: () => getJSON<AdminJob[]>("/api/admin/jobs"),
  startAdminJob: (command: string) =>
    postJSON<AdminJob>("/api/admin/jobs", { command }),
  stopAdminJob: (id: string) =>
    postJSON<AdminJob>(`/api/admin/jobs/${encodeURIComponent(id)}/stop`),
  adminHistory: () => getJSON<AdminCommand[]>("/api/admin/jobs/history"),
  deleteAdminHistory: (id: string) =>
    deleteJSON<AdminCommand[]>(`/api/admin/jobs/history/${encodeURIComponent(id)}`),
  globalChapterPatterns: () =>
    getJSON<GlobalChapterPattern[]>("/api/admin/chapter-patterns"),
  addGlobalChapterPattern: (label: string, pattern: string) =>
    postJSON<GlobalChapterPattern>("/api/admin/chapter-patterns", { label, pattern }),
  editGlobalChapterPattern: (id: string, label: string, pattern: string) =>
    putJSON<GlobalChapterPattern>(
      `/api/admin/chapter-patterns/${encodeURIComponent(id)}`,
      { label, pattern },
    ),
  deleteGlobalChapterPattern: (id: string) =>
    deleteJSON<{ ok: boolean }>(
      `/api/admin/chapter-patterns/${encodeURIComponent(id)}`,
    ),
};

async function readEventStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: string, data: any) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      let event = "message";
      let data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) {
        try {
          onEvent(event, JSON.parse(data));
        } catch {
          onEvent(event, { raw: data });
        }
      }
    }
  }
}

export async function adminLogStream(
  id: string,
  onEvent: (event: string, data: any) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await apiFetch(`/api/admin/jobs/${encodeURIComponent(id)}/log`, { signal });
  if (!res.ok) throw await responseError(res);
  if (!res.body) throw new Error("Log stream was unavailable");
  await readEventStream(res.body, onEvent);
}
