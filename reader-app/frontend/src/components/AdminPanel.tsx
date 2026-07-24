import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  adminLogStream,
  api,
  type AdminCommand,
  type AdminJob,
  type GlobalChapterPattern,
} from "../api/client";

interface Props {
  onClose: () => void;
}

const isLive = (status: string) => status === "running" || status === "stopping";

export function AdminPanel({ onClose }: Props) {
  const [command, setCommand] = useState("download categories gl --limit 1");
  const [jobs, setJobs] = useState<AdminJob[]>([]);
  const [history, setHistory] = useState<AdminCommand[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [patterns, setPatterns] = useState<GlobalChapterPattern[]>([]);
  const [patternId, setPatternId] = useState<string | null>(null);
  const [patternLabel, setPatternLabel] = useState("");
  const [patternRegex, setPatternRegex] = useState("");
  const [savingPattern, setSavingPattern] = useState(false);

  const refresh = useCallback(() => {
    api.adminJobs().then(setJobs).catch((e) => setError(e?.message ?? "Could not load jobs"));
  }, []);

  const refreshHistory = useCallback(() => {
    api.adminHistory().then(setHistory).catch(() => {});
  }, []);

  const refreshPatterns = useCallback(() => {
    api.globalChapterPatterns()
      .then(setPatterns)
      .catch((e) => setError(e?.message ?? "Could not load chapter patterns"));
  }, []);

  useEffect(() => {
    refresh();
    refreshHistory();
    refreshPatterns();
    const timer = window.setInterval(refresh, 2500);
    return () => window.clearInterval(timer);
  }, [refresh, refreshHistory, refreshPatterns]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setLog("");
    adminLogStream(
      selected,
      (event, data) => {
        if (event === "log") setLog((old) => old + String(data.text ?? ""));
        if (event === "done") refresh();
        if (event === "error") setError(data.message ?? "Log stream failed");
      },
      controller.signal,
    ).catch((e) => {
      if (e?.name !== "AbortError") setError(e?.message ?? "Log stream failed");
    });
    return () => controller.abort();
  }, [selected, refresh]);

  const start = async (event: FormEvent) => {
    event.preventDefault();
    setStarting(true);
    setError(null);
    try {
      const job = await api.startAdminJob(command);
      setSelected(job.id);
      refresh();
      refreshHistory();
    } catch (e: any) {
      setError(e?.message ?? "Could not start job");
    } finally {
      setStarting(false);
    }
  };

  const removeHistory = async (id: string) => {
    try {
      setHistory(await api.deleteAdminHistory(id));
    } catch {
      /* non-fatal */
    }
  };

  const stop = async (id: string) => {
    setError(null);
    try {
      await api.stopAdminJob(id);
      refresh();
    } catch (e: any) {
      setError(e?.message ?? "Could not stop job");
    }
  };

  const savePattern = async (event: FormEvent) => {
    event.preventDefault();
    setSavingPattern(true);
    setError(null);
    try {
      if (patternId) {
        await api.editGlobalChapterPattern(patternId, patternLabel, patternRegex);
      } else {
        await api.addGlobalChapterPattern(patternLabel, patternRegex);
      }
      setPatternId(null);
      setPatternLabel("");
      setPatternRegex("");
      refreshPatterns();
    } catch (e: any) {
      setError(e?.message ?? "Could not save chapter pattern");
    } finally {
      setSavingPattern(false);
    }
  };

  const editPattern = (pattern: GlobalChapterPattern) => {
    setPatternId(pattern.id);
    setPatternLabel(pattern.label);
    setPatternRegex(pattern.pattern);
  };

  const deletePattern = async (pattern: GlobalChapterPattern) => {
    if (!window.confirm(`Delete the global pattern “${pattern.label}”?`)) return;
    setError(null);
    try {
      await api.deleteGlobalChapterPattern(pattern.id);
      if (patternId === pattern.id) {
        setPatternId(null);
        setPatternLabel("");
        setPatternRegex("");
      }
      refreshPatterns();
    } catch (e: any) {
      setError(e?.message ?? "Could not delete chapter pattern");
    }
  };

  return (
    <div className="modal-layer" role="dialog" aria-modal="true" aria-label="Admin jobs">
      <button className="modal-scrim" onClick={onClose} aria-label="Close" />
      <section className="modal-card admin-card">
        <div className="modal-head">
          <div>
            <div className="section-label">Administrator</div>
            <h2 className="modal-title">Library administration</h2>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <form className="admin-command" onSubmit={start}>
          <input value={command} onChange={(e) => setCommand(e.target.value)} spellCheck={false} aria-label="Job command" />
          <button className="btn-seal" disabled={starting}>{starting ? "Starting…" : "Start"}</button>
        </form>
        <p className="admin-note">
          Allowed: scrape_metadata, download, recommend. Jobs survive closing this window.
          <code> --windscribe</code> is accepted but unavailable on the server.
        </p>

        <section className="admin-patterns">
          <div className="section-label">Global chapter heading regexes</div>
          <p className="admin-note">
            These patterns are tried for every book unless that book has its own regex.
          </p>
          <form className="admin-pattern-form" onSubmit={savePattern}>
            <input
              value={patternLabel}
              onChange={(event) => setPatternLabel(event.target.value)}
              placeholder="Pattern name"
              aria-label="Pattern name"
            />
            <input
              value={patternRegex}
              onChange={(event) => setPatternRegex(event.target.value)}
              placeholder="^\\s*Chapter\\s+\\d+.*$"
              spellCheck={false}
              aria-label="Chapter heading regex"
            />
            <button className="btn-seal" disabled={savingPattern}>
              {savingPattern ? "Saving…" : patternId ? "Update" : "Add"}
            </button>
            {patternId && (
              <button
                type="button"
                className="btn-outline"
                onClick={() => {
                  setPatternId(null);
                  setPatternLabel("");
                  setPatternRegex("");
                }}
              >
                Cancel
              </button>
            )}
          </form>
          <div className="admin-pattern-list">
            {patterns.map((pattern) => (
              <div className="admin-pattern-row" key={pattern.id}>
                <button
                  className="admin-pattern-edit"
                  onClick={() => editPattern(pattern)}
                  title="Edit pattern"
                >
                  <strong>{pattern.label}</strong>
                  {pattern.builtin && <em>built-in</em>}
                  <code>{pattern.pattern}</code>
                </button>
                <button
                  className="history-remove"
                  onClick={() => deletePattern(pattern)}
                  aria-label={`Delete ${pattern.label}`}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        </section>

        {history.length > 0 && (
          <div className="admin-history">
            <div className="section-label">Recent commands</div>
            <ul className="history-list">
              {history.map((h) => (
                <li key={h.id} className="history-item">
                  <button
                    className="history-cmd"
                    title="Use this command"
                    onClick={() => setCommand(h.command)}
                  >
                    {h.command}
                  </button>
                  <button
                    className="history-remove"
                    aria-label="Remove"
                    title="Remove from history"
                    onClick={() => removeHistory(h.id)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {error && <div className="form-error">{error}</div>}

        <div className="admin-grid">
          <div className="job-list">
            {jobs.map((job) => (
              <div key={job.id} className={`job-row${selected === job.id ? " is-on" : ""}`}>
                <button className="job-select" onClick={() => setSelected(job.id)}>
                  <span><strong>{job.script}</strong> {job.args.join(" ")}</span>
                  <span className={`job-status status-${job.status}`}>{job.status} · pid {job.pid}</span>
                </button>
                {isLive(job.status) && (
                  <button
                    className="job-stop"
                    onClick={() => stop(job.id)}
                  >Stop</button>
                )}
              </div>
            ))}
            {jobs.length === 0 && <div className="empty">No jobs yet.</div>}
          </div>
          <pre className="admin-log">{selected ? log || "Waiting for output…" : "Select a job to view its log."}</pre>
        </div>
      </section>
    </div>
  );
}
