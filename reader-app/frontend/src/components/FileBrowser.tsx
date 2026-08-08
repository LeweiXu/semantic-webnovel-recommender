import { useEffect, useRef, useState } from "react";
import { api, type BrowseEntry, type BrowseListing } from "../api/client";
import { useAuth } from "../store/auth";
import { libraryPath, navigate, novelPath, writeUrl } from "../routing";
import { UploadModal } from "./UploadModal";

// Split a browse path into breadcrumb crumbs: [{ label, path }], root first.
function crumbs(path: string): { label: string; path: string }[] {
  const out = [{ label: "Library", path: "" }];
  if (!path) return out;
  const parts = path.split("/");
  let acc = "";
  for (const part of parts) {
    acc = acc ? `${acc}/${part}` : part;
    out.push({ label: part, path: acc });
  }
  return out;
}

function fmtSize(bytes: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Fetch a downloadable file and save it through a temporary anchor (the blob
// carries the auth header a plain link can't).
async function saveDoc(entry: BrowseEntry) {
  const blob = await api.downloadFile(entry.path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = entry.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function FileBrowser({
  initialPath = "",
  onChange,
}: {
  initialPath?: string;
  onChange?: () => void;
}) {
  const user = useAuth((s) => s.user);
  const [path, setPath] = useState(initialPath);
  const [listing, setListing] = useState<BrowseListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [dragOver, setDragOver] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const pickFile = (file: File | null | undefined) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".txt")) {
      setError("Only .txt files can be uploaded.");
      return;
    }
    setError(null);
    setPendingFile(file);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (user) pickFile(e.dataTransfer.files?.[0]);
  };

  // Navigate to a folder: fetch it and reflect it in the URL (no history spam,
  // no page remount — writeUrl only touches the address bar).
  const goTo = (next: string) => {
    setPath(next);
    writeUrl(libraryPath("", next));
  };

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .browse(path)
      .then((data) => alive && setListing(data))
      .catch((e) => alive && setError(e?.message ?? "Could not open folder"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path]);

  const markAdded = (id: string) => setAdded((cur) => new Set(cur).add(id));

  const addEntry = (entry: BrowseEntry) => {
    api
      .addToShelf(entry.path)
      .then(() => {
        markAdded(entry.path);
        onChange?.();
      })
      .catch(() => {});
  };

  const downloadEntry = (entry: BrowseEntry) => {
    saveDoc(entry)
      .then(() => addEntry(entry))
      .catch(() => {});
  };

  return (
    <div
      className={`fb${dragOver ? " is-drag" : ""}`}
      onDragOver={user ? (e) => { e.preventDefault(); setDragOver(true); } : undefined}
      onDragLeave={user ? () => setDragOver(false) : undefined}
      onDrop={user ? onDrop : undefined}
    >
      <div className="fb-bar">
        <nav className="fb-crumbs" aria-label="Folder path">
          {crumbs(path).map((c, i, all) => (
            <span key={c.path} className="fb-crumb-wrap">
              {i > 0 && <span className="fb-sep" aria-hidden>/</span>}
              {i === all.length - 1 ? (
                <span className="fb-crumb is-current">{c.label}</span>
              ) : (
                <button className="fb-crumb" onClick={() => goTo(c.path)}>
                  {c.label}
                </button>
              )}
            </span>
          ))}
        </nav>
        {user && (
          <>
            <button className="fb-upload" onClick={() => fileInput.current?.click()} title="Upload a .txt novel">
              ↑ Upload .txt
            </button>
            <input
              ref={fileInput}
              type="file"
              accept=".txt,text/plain"
              hidden
              onChange={(e) => { pickFile(e.target.files?.[0]); e.target.value = ""; }}
            />
          </>
        )}
      </div>
      {dragOver && <div className="fb-drophint">Drop a .txt file to upload</div>}

      {error ? (
        <div className="fb-note">{error}</div>
      ) : loading && !listing ? (
        <div className="fb-note">Loading…</div>
      ) : listing && listing.entries.length === 0 ? (
        <div className="fb-note">This folder is empty.</div>
      ) : (
        <ul className="fb-list">
          {listing?.entries.map((entry) => (
            <li key={entry.path} className="fb-item">
              <button
                className={`fb-row fb-${entry.kind}`}
                onClick={() =>
                  entry.kind === "dir"
                    ? goTo(entry.path)
                    : entry.kind === "text"
                    ? navigate(novelPath(entry.path))
                    : undefined
                }
                disabled={entry.kind === "other"}
              >
                <span className="fb-icon" aria-hidden>
                  {entry.kind === "dir" ? "📁" : entry.kind === "text" ? "📄" : entry.kind === "doc" ? "📗" : "📦"}
                </span>
                <span className="fb-name">{entry.name}</span>
                {entry.kind === "text" && <span className="fb-kind">txt</span>}
                <span className="fb-size">{fmtSize(entry.size)}</span>
              </button>
              {entry.kind === "text" && (
                <button
                  className="fb-action"
                  onClick={() => addEntry(entry)}
                  disabled={added.has(entry.path)}
                  title="Add to your library"
                >
                  {added.has(entry.path) ? "✓" : "＋"}
                </button>
              )}
              {entry.kind === "doc" && (
                <button
                  className="fb-action"
                  onClick={() => downloadEntry(entry)}
                  title="Download and add to your library"
                >
                  {added.has(entry.path) ? "✓" : "Download"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {pendingFile && (
        <UploadModal
          file={pendingFile}
          onClose={() => setPendingFile(null)}
          onUploaded={() => {
            setPendingFile(null);
            goTo("uploads"); // show the new file where it landed
            onChange?.();
          }}
        />
      )}
    </div>
  );
}
