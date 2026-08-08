import { useEffect, useState } from "react";
import { api, type UploadResult } from "../api/client";

interface Props {
  file: File;
  onClose: () => void;
  onUploaded: (result: UploadResult) => void;
}

// Confirm-before-upload modal: autodetects metadata from the dropped .txt, then
// lets the user fix it before the file is written to library/uploads/.
export function UploadModal({ file, onClose, onUploaded }: Props) {
  const [detecting, setDetecting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [tags, setTags] = useState("");
  const [synopsis, setSynopsis] = useState("");
  const [status, setStatus] = useState("");
  const [language, setLanguage] = useState("zh");
  const [chapters, setChapters] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setDetecting(true);
    api
      .detectUpload(file)
      .then((m) => {
        if (!alive) return;
        setTitle(m.title);
        setAuthor(m.author);
        setTags(m.tags.join(", "));
        setSynopsis(m.synopsis);
        setStatus(m.status);
        setLanguage(m.language);
        setChapters(m.chapter_count);
      })
      .catch((e) => alive && setError(e?.message ?? "Could not read the file"))
      .finally(() => alive && setDetecting(false));
    return () => {
      alive = false;
    };
  }, [file]);

  const submit = () => {
    if (!title.trim()) {
      setError("A title is required.");
      return;
    }
    setBusy(true);
    setError(null);
    api
      .uploadTxt(file, { title, author, tags, synopsis, status })
      .then(onUploaded)
      .catch((e) => {
        setError(e?.message ?? "Upload failed");
        setBusy(false);
      });
  };

  return (
    <div className="modal-layer" role="dialog" aria-modal="true" aria-label="Upload novel">
      <button className="modal-scrim" onClick={onClose} aria-label="Close" />
      <section className="modal-card upload-card">
        <div className="modal-head">
          <div>
            <div className="section-label">Add to library</div>
            <h2 className="modal-title">Upload novel</h2>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="upload-file">
          {file.name}
          {chapters != null && <span className="upload-detected"> · {chapters} chapters · {language === "en" ? "EN" : "中文"}</span>}
        </div>

        {detecting ? (
          <div className="fb-note">Reading file…</div>
        ) : (
          <>
            <label className="upload-field">
              <span>Title</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} spellCheck={false} />
            </label>
            <label className="upload-field">
              <span>Author</span>
              <input value={author} onChange={(e) => setAuthor(e.target.value)} spellCheck={false} />
            </label>
            <div className="upload-row">
              <label className="upload-field">
                <span>Tags (comma-separated)</span>
                <input value={tags} onChange={(e) => setTags(e.target.value)} spellCheck={false} />
              </label>
              <label className="upload-field upload-status">
                <span>Status</span>
                <input value={status} onChange={(e) => setStatus(e.target.value)} spellCheck={false} />
              </label>
            </div>
            <label className="upload-field">
              <span>Synopsis</span>
              <textarea value={synopsis} onChange={(e) => setSynopsis(e.target.value)} rows={4} spellCheck={false} />
            </label>
          </>
        )}

        {error && <div className="upload-error">{error}</div>}

        <div className="upload-actions">
          <button className="btn-outline" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-seal" onClick={submit} disabled={busy || detecting}>
            {busy ? "Uploading…" : "Upload"}
          </button>
        </div>
      </section>
    </div>
  );
}
