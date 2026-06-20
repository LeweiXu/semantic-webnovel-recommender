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
