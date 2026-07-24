import { useEffect, useState } from "react";
import {
  api,
  type ChapterPatternResult,
  type NovelDetail,
} from "../api/client";
import { useAuth } from "../store/auth";

interface Props {
  novel: NovelDetail;
  onClose: () => void;
  onApplied: (chapter: number) => void;
}

export function ChapterPatternDialog({ novel, onClose, onApplied }: Props) {
  const user = useAuth((state) => state.user);
  const [sample, setSample] = useState(novel.chapter_examples.join("\n"));
  const [pattern, setPattern] = useState(novel.chapter_pattern ?? "");
  const [preview, setPreview] = useState<ChapterPatternResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const runPreview = async (useCurrentPattern: boolean) => {
    if (!user) {
      setError("Log in before changing shared chapter detection.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.previewChapterPattern(
        novel.slug,
        sample,
        useCurrentPattern ? pattern : "",
      );
      setPattern(result.pattern);
      setPreview(result);
    } catch (reason: any) {
      setPreview(null);
      setError(reason?.message ?? "Could not preview that pattern");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.saveChapterPattern(novel.slug, pattern, sample);
      onApplied(result.selected_chapter);
    } catch (reason: any) {
      setError(reason?.message ?? "Could not save that pattern");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteChapterPattern(novel.slug);
      onApplied(0);
    } catch (reason: any) {
      setError(reason?.message ?? "Could not remove that pattern");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-layer" role="dialog" aria-modal="true" aria-label="Chapter detection">
      <button className="modal-scrim" onClick={onClose} aria-label="Close" />
      <section className="modal-card chapter-pattern-card">
        <div className="modal-head">
          <div>
            <div className="section-label">Shared book correction</div>
            <h2 className="modal-title">Chapter detection</h2>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <p className="chapter-pattern-note">
          This regex is saved for this book and applied for every reader. It only
          runs against short, complete lines.
        </p>

        <label className="chapter-pattern-field">
          Heading examples
          <textarea
            value={sample}
            onChange={(event) => {
              setSample(event.target.value);
              setPreview(null);
            }}
            placeholder={"Paste one chapter heading per line, for example:\n1重生\n2归来\n3终章"}
            rows={5}
          />
        </label>
        <label className="chapter-pattern-field">
          Heading regex
          <input
            value={pattern}
            onChange={(event) => {
              setPattern(event.target.value);
              setPreview(null);
            }}
            placeholder={sample ? "Generate from examples or enter a regex" : "Paste examples above"}
            spellCheck={false}
          />
        </label>

        <div className="chapter-pattern-actions">
          <button
            className="btn-outline"
            disabled={busy || !sample.trim()}
            onClick={() => runPreview(false)}
          >
            {busy ? "Checking…" : "Generate from examples"}
          </button>
          <button
            className="btn-outline"
            disabled={busy || !pattern.trim()}
            onClick={() => runPreview(true)}
          >
            Preview regex
          </button>
          <button className="btn-seal" disabled={busy || !preview} onClick={save}>
            Apply to book
          </button>
          {novel.chapter_pattern && (
            <button className="chapter-pattern-remove" disabled={busy} onClick={remove}>
              Remove custom regex
            </button>
          )}
        </div>

        {preview && (
          <div className="chapter-pattern-preview">
            <strong>{preview.matches} headings found</strong>
            <ul>
              {preview.examples.map((example, index) => <li key={index}>{example}</li>)}
            </ul>
          </div>
        )}
        {error && <div className="form-error">{error}</div>}
      </section>
    </div>
  );
}
