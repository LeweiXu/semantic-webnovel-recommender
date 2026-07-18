import { useEffect } from "react";
import { useDownloads } from "../store/downloads";

interface Props {
  url: string;
  onDone: (nid: string) => void;
}

// Inline download row shown when the search box holds a 52shuku URL. Runs through
// the shared downloads store so progress shows live and the novel lands on the
// shelf as it downloads.
export function DownloadDialog({ url, onDone }: Props) {
  const start = useDownloads((s) => s.start);
  const dl = useDownloads((s) => s.map[url]);

  const running = dl?.status === "queued" || dl?.status === "running";
  const pctDone = dl && dl.total > 0 ? Math.round((dl.done / dl.total) * 100) : 0;
  const label = !dl
    ? ""
    : dl.status === "error"
    ? dl.error
    : dl.status === "queued"
    ? "Queued…"
    : dl.status === "done"
    ? `Saved · ${dl.total} chapters`
    : dl.total > 0
    ? `Downloading ${dl.done}/${dl.total}`
    : "Downloading…";

  useEffect(() => {
    if (dl?.status === "done") onDone(dl.slug || dl.nid || url);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dl?.status]);

  return (
    <div className="download">
      <div className="download-head">
        <div>
          <div className="download-label">New novel</div>
          <div className="download-url">{url}</div>
        </div>
        <button className="btn-seal" onClick={() => start(url)} disabled={running}>
          {running ? "Downloading…" : "Download"}
        </button>
      </div>
      {dl && (
        <div className={`download-log${dl.status === "error" ? " is-error" : ""}`}>
          {running && dl.total > 0 && (
            <span className="rec-dl-bar" aria-hidden>
              <span style={{ width: `${pctDone}%` }} />
            </span>
          )}
          {label}
        </div>
      )}
    </div>
  );
}
