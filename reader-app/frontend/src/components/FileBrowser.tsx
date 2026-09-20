import { useEffect, useRef, useState } from "react";
import { api, type BrowseEntry, type BrowseListing } from "../api/client";
import { useAuth } from "../store/auth";
import { libraryPath, navigate, novelPath, writeUrl } from "../routing";
import { UploadModal } from "./UploadModal";

// How long a touch has to be held before it counts as a right-click.
const LONG_PRESS_MS = 500;
// Keep the menu off the very edge of the screen.
const MENU_MARGIN = 8;
const MENU_SIZE = { width: 190, height: 240 };

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

function stemOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

interface MenuAt {
  entry: BrowseEntry;
  x: number;
  y: number;
}

export function FileBrowser({
  initialPath = "",
  onChange,
}: {
  initialPath?: string;
  onChange?: () => void;
}) {
  const user = useAuth((s) => s.user);
  const isAdmin = user?.username === "lingwei";
  const [path, setPath] = useState(initialPath);
  const [listing, setListing] = useState<BrowseListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [menu, setMenu] = useState<MenuAt | null>(null);
  const [renaming, setRenaming] = useState<{ entry: BrowseEntry; value: string } | null>(null);
  const [deleting, setDeleting] = useState<BrowseEntry | null>(null);
  const [reload, setReload] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const longPress = useRef<number | null>(null);

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
  }, [path, reload]);

  // The menu is anchored to a point on screen, so anything that moves the page
  // under it closes it rather than leaving it stranded.
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const openMenu = (entry: BrowseEntry, x: number, y: number) => {
    setNote(null);
    setMenu({
      entry,
      x: Math.min(x, window.innerWidth - MENU_SIZE.width - MENU_MARGIN),
      y: Math.min(y, window.innerHeight - MENU_SIZE.height - MENU_MARGIN),
    });
  };

  // Touch has no right-click, so a long press stands in for it.
  const startLongPress = (entry: BrowseEntry, e: React.TouchEvent) => {
    const touch = e.touches[0];
    if (!touch) return;
    const { clientX, clientY } = touch;
    longPress.current = window.setTimeout(
      () => openMenu(entry, clientX, clientY),
      LONG_PRESS_MS,
    );
  };
  const cancelLongPress = () => {
    if (longPress.current) window.clearTimeout(longPress.current);
    longPress.current = null;
  };

  const openEntry = (entry: BrowseEntry) => {
    if (entry.kind === "dir") goTo(entry.path);
    else if (entry.kind === "text") navigate(novelPath(entry.path));
  };

  const addEntry = (entry: BrowseEntry) => {
    api
      .addToShelf(entry.path)
      .then(() => {
        setNote(`Added ${entry.name} to your library.`);
        onChange?.();
      })
      .catch((e) => setNote(e?.message ?? "Could not add that."));
  };

  const saveEntry = (entry: BrowseEntry) => {
    api.saveFile(entry.path, entry.name).catch((e) => setNote(e?.message ?? "Download failed."));
  };

  const doRename = () => {
    if (!renaming) return;
    const { entry, value } = renaming;
    setRenaming(null);
    api
      .renameFile(entry.path, value)
      .then((r) => {
        setNote(
          r.indexed
            ? `Renamed. Its library record moved with it.`
            : `Renamed to ${r.path.split("/").pop()}.`,
        );
        setReload((n) => n + 1);
        onChange?.();
      })
      .catch((e) => setNote(e?.message ?? "Could not rename that."));
  };

  const doDelete = () => {
    if (!deleting) return;
    const entry = deleting;
    setDeleting(null);
    api
      .deleteFile(entry.path)
      .then((r) => {
        setNote(
          r.indexed
            ? `Deleted ${entry.name} and its library record.`
            : `Deleted ${entry.name}.`,
        );
        setReload((n) => n + 1);
        onChange?.();
      })
      .catch((e) => setNote(e?.message ?? "Could not delete that."));
  };

  const canEdit = (entry: BrowseEntry) => isAdmin && entry.managed && entry.kind !== "dir";

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
      <p className="fb-hint">Right-click a file for more (long-press on a phone).</p>
      {dragOver && <div className="fb-drophint">Drop a .txt file to upload</div>}
      {note && <div className="fb-note is-status" role="status">{note}</div>}

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
                onClick={() => openEntry(entry)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  openMenu(entry, e.clientX, e.clientY);
                }}
                onTouchStart={(e) => startLongPress(entry, e)}
                onTouchMove={cancelLongPress}
                onTouchEnd={cancelLongPress}
                disabled={entry.kind === "other"}
              >
                <span className="fb-icon" aria-hidden>
                  {entry.kind === "dir" ? "📁" : entry.kind === "text" ? "📄" : entry.kind === "doc" ? "📗" : "📦"}
                </span>
                <span className="fb-name">{entry.name}</span>
                {entry.kind === "text" && <span className="fb-kind">txt</span>}
                <span className="fb-size">{fmtSize(entry.size)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {menu && (
        <div
          className="fb-menu"
          style={{ left: menu.x, top: menu.y }}
          role="menu"
          // The window listener above closes the menu; without this a click on
          // the menu itself would bubble up and shut it before it acted.
          onClick={(e) => e.stopPropagation()}
        >
          <div className="fb-menu-head">{menu.entry.name}</div>
          {menu.entry.kind !== "other" && (
            <button role="menuitem" onClick={() => { setMenu(null); openEntry(menu.entry); }}>
              {menu.entry.kind === "dir" ? "Open folder" : "Open"}
            </button>
          )}
          {menu.entry.kind !== "dir" && (
            <>
              {user && (
                <button role="menuitem" onClick={() => { setMenu(null); addEntry(menu.entry); }}>
                  Add to library
                </button>
              )}
              <button role="menuitem" onClick={() => { setMenu(null); saveEntry(menu.entry); }}>
                Download
              </button>
            </>
          )}
          {canEdit(menu.entry) && (
            <>
              <div className="fb-menu-rule" />
              <button
                role="menuitem"
                onClick={() => {
                  setRenaming({ entry: menu.entry, value: stemOf(menu.entry.name) });
                  setMenu(null);
                }}
              >
                Rename…
              </button>
              <button
                role="menuitem"
                className="is-danger"
                onClick={() => { setDeleting(menu.entry); setMenu(null); }}
              >
                Delete…
              </button>
            </>
          )}
        </div>
      )}

      {renaming && (
        <div className="fb-modal-scrim" onClick={() => setRenaming(null)}>
          <div className="fb-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Rename file</h3>
            <p className="fb-modal-sub">The .txt extension is kept for you.</p>
            <input
              className="search"
              autoFocus
              value={renaming.value}
              onChange={(e) => setRenaming({ ...renaming, value: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter") doRename();
                if (e.key === "Escape") setRenaming(null);
              }}
            />
            <div className="confirm-row">
              <button className="btn-outline is-danger" disabled={!renaming.value.trim()} onClick={doRename}>
                Rename
              </button>
              <button className="btn-outline" onClick={() => setRenaming(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {deleting && (
        <div className="fb-modal-scrim" onClick={() => setDeleting(null)}>
          <div className="fb-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Delete file</h3>
            <p className="fb-modal-sub">
              {deleting.name} will be removed from disk. This cannot be undone.
            </p>
            <div className="confirm-row">
              <button className="btn-outline is-danger" onClick={doDelete}>Delete</button>
              <button className="btn-outline" onClick={() => setDeleting(null)}>Cancel</button>
            </div>
          </div>
        </div>
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
