import { create } from "zustand";
import { api, type DownloadDTO } from "../api/client";

// Downloads run server-side (a queue that survives reloads); this store just
// mirrors them by polling GET /api/downloads while any are active, keyed by url.
export type DlState = DownloadDTO;

interface DownloadsStore {
  map: Record<string, DlState>;
  start: (url: string) => Promise<void>;
  init: () => void; // begin mirroring (call on login / app load)
  poll: () => Promise<void>;
  clear: (url: string) => void;
}

let timer: number | null = null;

export const useDownloads = create<DownloadsStore>((set, get) => {
  const merge = (list: DlState[]): boolean => {
    set((s) => {
      const map = { ...s.map };
      for (const d of list) map[d.url] = d;
      return { map };
    });
    return list.some((d) => d.status === "queued" || d.status === "running");
  };
  const schedule = () => {
    if (timer == null) timer = window.setInterval(() => void get().poll(), 1500);
  };
  const stop = () => {
    if (timer != null) {
      window.clearInterval(timer);
      timer = null;
    }
  };

  return {
    map: {},
    poll: async () => {
      try {
        const active = merge(await api.downloads());
        if (active) schedule();
        else stop();
      } catch {
        stop();
      }
    },
    start: async (url) => {
      try {
        const state = await api.startDownload(url);
        merge([state]);
        schedule();
      } catch (e: any) {
        set((s) => ({
          map: {
            ...s.map,
            [url]: { url, nid: "", title: url, status: "error", done: 0, total: 0, slug: null, error: e?.message ?? "Download failed" },
          },
        }));
      }
    },
    init: () => {
      void get().poll();
    },
    clear: (url) =>
      set((s) => {
        const map = { ...s.map };
        delete map[url];
        return { map };
      }),
  };
});
