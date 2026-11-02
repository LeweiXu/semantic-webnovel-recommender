import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Props {
  path: string; // browse path of an uploads .txt
  name: string;
  onClose: () => void;
  onSaved: (title: string) => void;
}

// Admin-only: edit an uploaded novel's library details (title, author, tags,
// status, synopsis). A file dropped in by hand has no record yet; the form
// starts from a guess and saving creates the record.
export function EditDetailsModal({ path, name, onClose, onSaved }: Props) {
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [indexed, setIndexed] = useState(true);

  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [tags, setTags] = useState("");
  const [status, setStatus] = useState("");
  const [synopsis, setSynopsis] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .fileMeta(path)
      .then((meta) => {
        if (!alive) return;
        setTitle(meta.title);
        setAuthor(meta.author);
        setTags(meta.tags.join(", "));
        setStatus(meta.status);
        setSynopsis(meta.synopsis);
        setIndexed(meta.indexed);
      })
      .catch((e) => alive && setError(e?.message ?? "Could not load the details"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const save = () => {
    if (!title.trim()) {
      setError("A title is required.");
      return;
    }
    setBusy(true);
    setError(null);
    api
      .saveFileMeta({
        path,
        title,
        author,
        tags: tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean),
        status,
        synopsis,
      })
      .then((meta) => onSaved(meta.title))
      .catch((e) => {
        setError(e?.message ?? "Could not save the details");
        setBusy(false);
      });
  };

  return (
    <div className="modal-layer" role="dialog" aria-modal="true" aria-label="Edit details">
      <button className="modal-scrim" onClick={() => !busy && onClose()} aria-label="Close" />
      <section className="modal-card upload-card">
        <div className="modal-head">
          <div>
            <div className="section-label">Library details</div>
            <h2 className="modal-title">Edit details</h2>
          </div>
          <button className="modal-close" onClick={onClose} disabled={busy} aria-label="Close">×</button>
        </div>
        <div className="upload-file">{name}</div>
        {!loading && !indexed && (
          <div className="upload-dest">Not in the library index yet; these are guesses, and saving adds it.</div>
        )}

        {loading ? (
          <div className="fb-note">Loading…</div>
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
              <textarea value={synopsis} onChange={(e) => setSynopsis(e.target.value)} rows={6} spellCheck={false} />
            </label>
          </>
        )}

        {error && <div className="upload-error">{error}</div>}

        <div className="upload-actions">
          <button className="btn-outline" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-seal" onClick={save} disabled={busy || loading}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </section>
    </div>
  );
}
