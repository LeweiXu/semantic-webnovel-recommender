import { useCallback, useEffect, useState, type FormEvent } from "react";
import { adminLogStream, api, type AdminCommand, type AdminJob } from "../api/client";

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

  const refresh = useCallback(() => {
    api.adminJobs().then(setJobs).catch((e) => setError(e?.message ?? "Could not load jobs"));
  }, []);

  const refreshHistory = useCallback(() => {
    api.adminHistory().then(setHistory).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    refreshHistory();
    const timer = window.setInterval(refresh, 2500);
    return () => window.clearInterval(timer);
  }, [refresh, refreshHistory]);

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

  return (
    <div className="modal-layer" role="dialog" aria-modal="true" aria-label="Admin jobs">
      <button className="modal-scrim" onClick={onClose} aria-label="Close" />
      <section className="modal-card admin-card">
        <div className="modal-head">
          <div>
            <div className="section-label">Administrator</div>
            <h2 className="modal-title">Library jobs</h2>
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
