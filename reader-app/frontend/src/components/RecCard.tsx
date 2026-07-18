import { useState } from "react";
import { type RecItem } from "../api/client";
import { useReader } from "../store/reader";
import { useAuth } from "../store/auth";
import { useDownloads } from "../store/downloads";

interface Props {
  rec: RecItem;
  delay: number;
  onSimilar: (nid: string, title: string) => void;
  onTag: (tag: string) => void;
}

const CAT_LABEL: Record<string, string> = { gl: "百合", yanqing: "言情" };
const catLabel = (c: string) => CAT_LABEL[c] ?? c;

// A single recommendation. Non-downloaded novels offer a Download action that
// streams the real scraper over SSE (via the shared downloads store) and adds
// the novel to your library right away, showing live progress here and there.
export function RecCard({ rec, delay, onSimilar, onTag }: Props) {
  const openNovel = useReader((s) => s.openNovel);
  const user = useAuth((s) => s.user);
  const start = useDownloads((s) => s.start);
  const dl = useDownloads((s) => s.map[rec.url]);
  const [note, setNote] = useState("");

  const downloaded = rec.downloaded || dl?.status === "done";
  const running = dl?.status === "queued" || dl?.status === "running";
  const pctDone = dl && dl.total > 0 ? Math.round((dl.done / dl.total) * 100) : 0;
  const dlLabel = !dl
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

  const onDownload = () => {
    if (!user) {
      setNote("Log in from the account button to download novels.");
      return;
    }
    setNote("");
    start(rec.url);
  };

  return (
    <li className="rec-card" style={{ animationDelay: `${delay}ms` }}>
      <div className="rec-head">
        <span className="rec-title">{rec.title}</span>
        <span className={`rec-cat cat-${rec.category}`}>{catLabel(rec.category)}</span>
      </div>
      <div className="rec-meta">
        {rec.author || "—"}
        {rec.status ? ` · ${rec.status}` : ""}
      </div>
      <div className="rec-bar" style={{ ["--w" as any]: `${rec.similarity}%` }}>
        <span />
        <em>{rec.similarity}%</em>
      </div>
      {rec.synopsis && <p className="rec-syn">{rec.synopsis}</p>}
      {rec.tags.length > 0 && (
        <div className="rec-tags">
          {rec.tags.map((t) => (
            <button key={t} className="rec-tag" onClick={() => onTag(t)}>
              {t}
            </button>
          ))}
        </div>
      )}
      <div className="rec-actions">
        <button className="rec-btn" onClick={() => onSimilar(rec.nid, rec.title)}>
          ✦ Similar
        </button>
        {downloaded ? (
          <button className="rec-btn primary" onClick={() => openNovel(rec.slug ?? dl?.slug ?? rec.nid)}>
            Read
          </button>
        ) : (
          <button className="rec-btn primary" onClick={onDownload} disabled={running}>
            {running ? (dl && dl.total > 0 ? `Downloading… ${dl.done}/${dl.total}` : "Downloading…") : "Download"}
          </button>
        )}
        <a className="rec-btn ghost" href={rec.url} target="_blank" rel="noreferrer">
          52shuku ↗
        </a>
      </div>
      {(dl || note) && (
        <div className={`rec-dl${dl?.status === "error" ? " is-error" : ""}${downloaded ? " is-done" : ""}`}>
          {running && dl && dl.total > 0 && (
            <span className="rec-dl-bar" aria-hidden>
              <span style={{ width: `${pctDone}%` }} />
            </span>
          )}
          <span className="rec-dl-msg">{note || dlLabel}</span>
        </div>
      )}
    </li>
  );
}
