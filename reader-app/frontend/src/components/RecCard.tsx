import { useState } from "react";
import { downloadStream, type RecItem } from "../api/client";
import { useReader } from "../store/reader";

interface Props {
  rec: RecItem;
  delay: number;
  onSimilar: (nid: string, title: string) => void;
  onTag: (tag: string) => void;
}

const CAT_LABEL: Record<string, string> = { gl: "百合", yanqing: "言情" };
const catLabel = (c: string) => CAT_LABEL[c] ?? c;

// A single recommendation. Non-downloaded novels offer a Download action that
// streams the real scraper over SSE (writing into library/<category>/<month>/),
// then unlocks Read — a proof-of-concept of going demo → download → read.
export function RecCard({ rec, delay, onSimilar, onTag }: Props) {
  const openNovel = useReader((s) => s.openNovel);
  const [dl, setDl] = useState<"idle" | "running" | "error" | "done">("idle");
  const [line, setLine] = useState("");

  const downloaded = rec.downloaded || dl === "done";

  const download = async () => {
    setDl("running");
    setLine("Starting download…");
    try {
      await downloadStream(rec.url, (event, data) => {
        if (event === "progress") setLine(String(data.message).slice(0, 90));
        else if (event === "done") {
          setDl("done");
          setLine(`Saved · ${data.chapters} chapters`);
        } else if (event === "error") {
          setDl("error");
          setLine(data.message);
        }
      });
    } catch (e: any) {
      setDl("error");
      setLine(e?.message ?? "Download failed");
    }
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
          <button className="rec-btn primary" onClick={() => openNovel(rec.nid)}>
            Read
          </button>
        ) : (
          <button className="rec-btn primary" onClick={download} disabled={dl === "running"}>
            {dl === "running" ? "Downloading…" : "Download"}
          </button>
        )}
        <a className="rec-btn ghost" href={rec.url} target="_blank" rel="noreferrer">
          52shuku ↗
        </a>
      </div>
      {dl !== "idle" && (
        <div className={`rec-dl${dl === "error" ? " is-error" : ""}${dl === "done" ? " is-done" : ""}`}>
          {line}
        </div>
      )}
    </li>
  );
}
