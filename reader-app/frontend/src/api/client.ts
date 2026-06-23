// Typed access to the reader backend. Same-origin in production; the Vite dev
// server proxies /api to the FastAPI backend.

export interface ReadingItem {
  url: string;
  nid: string;
  title: string;
  author: string;
  category: string;
  position: number;
  total: number | null;
  updated: string;
}

export interface SearchItem {
  url: string;
  nid: string;
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
  title: string;
  author: string;
  category: string;
  synopsis: string;
  downloaded: boolean;
  total: number;
  position: number;
  chapters: ChapterStub[];
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

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export const api = {
  reading: () => getJSON<ReadingItem[]>("/api/library/reading"),
  search: (q: string) =>
    getJSON<SearchItem[]>(`/api/library/search?q=${encodeURIComponent(q)}`),
  novel: (nid: string) => getJSON<NovelDetail>(`/api/novel/${nid}`),
  chapter: (nid: string, idx: number, annotate: boolean) =>
    getJSON<ChapterContent>(
      `/api/novel/${nid}/chapter/${idx}?annotate=${annotate ? 1 : 0}`,
    ),
  define: (word: string) =>
    getJSON<DefineOut>(`/api/define?word=${encodeURIComponent(word)}`),
  recommend: (q: string, n = 12, category?: string) =>
    getJSON<{ results: RecItem[] }>(
      `/api/recommend?q=${encodeURIComponent(q)}&n=${n}` +
        (category ? `&category=${category}` : ""),
    ),
  similar: (nid: string, n = 12, category?: string) =>
    getJSON<SimilarOut>(
      `/api/similar/${nid}?n=${n}` + (category ? `&category=${category}` : ""),
    ),
  discoverMap: () => getJSON<MapData>(`/api/discover/map`),
  discoverTags: (limit = 18) => getJSON<TagCount[]>(`/api/discover/tags?limit=${limit}`),
  setProgress: async (nid: string, position: number) => {
    const res = await fetch(`/api/novel/${nid}/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ position }),
    });
    if (!res.ok) throw new Error(`progress ${res.status}`);
    return res.json() as Promise<{ ok: boolean; position: number; updated: string }>;
  },
};

// Stream a download as Server-Sent Events. Calls onEvent for each parsed event;
// resolves when the stream closes.
export async function downloadStream(
  url: string,
  onEvent: (event: string, data: any) => void,
): Promise<void> {
  const res = await fetch("/api/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok || !res.body) throw new Error(`download ${res.status}`);
  const reader = res.body.getReader();
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
