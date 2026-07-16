import { useState } from "react";
import { downloadStream } from "../api/client";

interface Props {
  url: string;
  onDone: (nid: string) => void;
}

// Inline download row shown when the search box holds a 52shuku URL. Streams
// live scraper progress over SSE.
export function DownloadDialog({ url, onDone }: Props) {
  const [status, setStatus] = useState<"idle" | "running" | "error">("idle");
  const [lines, setLines] = useState<string[]>([]);

  const start = async () => {
    setStatus("running");
    setLines([]);
    try {
      await downloadStream(url, (event, data) => {
        if (event === "progress") {
          setLines((prev) => [...prev.slice(-40), data.message]);
        } else if (event === "done") {
          setLines((prev) => [...prev, `Saved ${data.title} (${data.chapters} chapters)`]);
          setStatus("idle");
          onDone(data.slug || data.nid);
        } else if (event === "error") {
          setLines((prev) => [...prev, data.message]);
          setStatus("error");
        }
      });
    } catch (e: any) {
      setLines((prev) => [...prev, e?.message ?? "Download failed"]);
      setStatus("error");
    }
  };

  return (
    <div className="download">
      <div className="download-head">
        <div>
          <div className="download-label">New novel</div>
          <div className="download-url">{url}</div>
        </div>
        <button
          className="btn-seal"
          onClick={start}
          disabled={status === "running"}
        >
          {status === "running" ? "Downloading…" : "Download"}
        </button>
      </div>
      {lines.length > 0 && (
        <pre className={`download-log${status === "error" ? " is-error" : ""}`}>
          {lines.join("\n")}
        </pre>
      )}
    </div>
  );
}
