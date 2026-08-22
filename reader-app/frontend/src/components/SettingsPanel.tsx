import { useEffect, useState } from "react";
import { useSettings, useActiveSettings, type Theme } from "../store/settings";
import { useReader } from "../store/reader";

const THEMES: { id: Theme; label: string }[] = [
  { id: "paper", label: "Paper" },
  { id: "sepia", label: "Sepia" },
  { id: "night", label: "Night" },
  { id: "black", label: "Black" },
];

// How long an armed confirmation waits before disarming itself.
const CONFIRM_TIMEOUT_MS = 6000;

// A button that asks before it acts. Both actions here throw away state the
// user can't get back, so neither should fire on a single stray tap. Inline
// rather than window.confirm: it matches the panel and works the same on a
// phone, where a native dialog is easy to dismiss by accident.
function ConfirmButton({
  label,
  confirmLabel,
  onConfirm,
}: {
  label: string;
  confirmLabel: string;
  onConfirm: () => void;
}) {
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    if (!armed) return;
    const timer = window.setTimeout(() => setArmed(false), CONFIRM_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [armed]);

  if (!armed) {
    return (
      <button className="btn-outline" onClick={() => setArmed(true)}>
        {label}
      </button>
    );
  }
  return (
    <div className="confirm-row" role="group" aria-label={`Confirm: ${label}`}>
      <button
        className="btn-outline is-danger"
        autoFocus
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
      >
        {confirmLabel}
      </button>
      <button className="btn-outline" onClick={() => setArmed(false)}>
        Cancel
      </button>
    </div>
  );
}

export function SettingsPanel() {
  const s = useActiveSettings();
  const set = useSettings((st) => st.set);
  const reset = useSettings((st) => st.reset);
  const novel = useReader((st) => st.novel);
  const view = useReader((st) => st.view);
  const resetProgress = useReader((st) => st.resetProgressToCurrent);
  const openChapterPattern = useReader((st) => st.openChapterPattern);
  // Pinyin controls are meaningless for an English novel — hide them while one
  // is open. They stay visible everywhere else (and for Chinese novels).
  const showPinyin = novel?.language !== "en";

  return (
    <div className="panel">
      <div className="panel-head">
        <h2 className="panel-title">Reading</h2>
      </div>

      {showPinyin && (
        <div className="setting">
          <label className="setting-label">Pinyin</label>
          <button
            className={`toggle${s.pinyin ? " is-on" : ""}`}
            role="switch"
            aria-checked={s.pinyin}
            onClick={() => set({ pinyin: !s.pinyin })}
          >
            <span className="toggle-knob" />
          </button>
        </div>
      )}

      {showPinyin && (
        <div className="setting">
          <label className="setting-label">Synopsis pinyin</label>
          <button
            className={`toggle${s.synopsisPinyin ? " is-on" : ""}`}
            role="switch"
            aria-checked={s.synopsisPinyin}
            onClick={() => set({ synopsisPinyin: !s.synopsisPinyin })}
          >
            <span className="toggle-knob" />
          </button>
        </div>
      )}

      <div className="setting setting-block">
        <label className="setting-label">Theme</label>
        <div className="segmented">
          {THEMES.map((t) => (
            <button
              key={t.id}
              className={`segment${s.theme === t.id ? " is-on" : ""}`}
              onClick={() => set({ theme: t.id })}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="setting setting-block">
        <label className="setting-label">
          Text size <span className="setting-value">{s.fontSize}px</span>
        </label>
        <input
          type="range"
          min={16}
          max={30}
          step={1}
          value={s.fontSize}
          onChange={(e) => set({ fontSize: Number(e.target.value) })}
        />
      </div>

      <div className="setting setting-block">
        <label className="setting-label">
          Line spacing <span className="setting-value">{s.leading.toFixed(2)}</span>
        </label>
        <input
          type="range"
          min={1.0}
          max={3.0}
          step={0.05}
          value={s.leading}
          onChange={(e) => set({ leading: Number(e.target.value) })}
        />
      </div>

      <div className="setting setting-block">
        <label className="setting-label">
          Character spacing <span className="setting-value">{s.tracking.toFixed(2)}em</span>
        </label>
        <input
          type="range"
          min={0}
          max={0.3}
          step={0.01}
          value={s.tracking}
          onChange={(e) => set({ tracking: Number(e.target.value) })}
        />
      </div>

      <div className="setting setting-block">
        <label className="setting-label">
          Column width <span className="setting-value">{s.measure}rem</span>
        </label>
        <input
          type="range"
          min={28}
          max={100}
          step={1}
          value={s.measure}
          onChange={(e) => set({ measure: Number(e.target.value) })}
        />
      </div>

      <div className="setting setting-block">
        <label className="setting-label">
          Contrast <span className="setting-value">{s.contrast}%</span>
        </label>
        <input
          type="range"
          min={50}
          max={150}
          step={5}
          value={s.contrast}
          onChange={(e) => set({ contrast: Number(e.target.value) })}
          aria-label="Text and background contrast"
        />
      </div>

      <div className="setting">
        <label className="setting-label">Infinite scroll</label>
        <button
          className={`toggle${s.infiniteScroll ? " is-on" : ""}`}
          role="switch"
          aria-checked={s.infiniteScroll}
          onClick={() => set({ infiniteScroll: !s.infiniteScroll })}
        >
          <span className="toggle-knob" />
        </button>
      </div>

      <div className="setting setting-block">
        <ConfirmButton
          label="Reset to defaults"
          confirmLabel="Reset settings"
          onConfirm={() => reset()}
        />
      </div>

      {novel && (
        <div className="setting setting-block">
          <label className="setting-label">Progress</label>
          <ConfirmButton
            label="Reset Progress To Current"
            confirmLabel="Reset progress"
            onConfirm={() => void resetProgress()}
          />
        </div>
      )}

      {novel && view === "read" && (
        <div className="setting setting-block">
          <label className="setting-label">
            Book structure
            <span className="setting-value">
              {novel.chapter_pattern ? "Custom" : novel.chapter_mode}
            </span>
          </label>
          <button className="btn-outline" onClick={openChapterPattern}>
            Chapter Heading Regex
          </button>
        </div>
      )}
    </div>
  );
}
