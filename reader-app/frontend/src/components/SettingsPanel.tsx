import { useSettings, useActiveSettings, type Theme, type ReadingMode } from "../store/settings";
import { useReader } from "../store/reader";

const THEMES: { id: Theme; label: string }[] = [
  { id: "paper", label: "Paper" },
  { id: "sepia", label: "Sepia" },
  { id: "night", label: "Night" },
  { id: "black", label: "Black" },
];

export function SettingsPanel() {
  const s = useActiveSettings();
  const set = useSettings((st) => st.set);
  const reset = useSettings((st) => st.reset);
  const novel = useReader((st) => st.novel);
  const resetProgress = useReader((st) => st.resetProgressToCurrent);

  return (
    <div className="panel">
      <div className="panel-head">
        <h2 className="panel-title">Reading</h2>
      </div>

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

      <div className="setting setting-block">
        <label className="setting-label">Reading mode</label>
        <div className="segmented">
          {(["scroll", "paginate"] as ReadingMode[]).map((m) => (
            <button
              key={m}
              className={`segment${s.mode === m ? " is-on" : ""}`}
              disabled={m === "paginate"}
              title={m === "paginate" ? "Coming soon" : ""}
              onClick={() => set({ mode: m })}
            >
              {m === "scroll" ? "Scroll" : "Paginate"}
              {m === "paginate" && <em className="soon">soon</em>}
            </button>
          ))}
        </div>
      </div>

      <div className="setting setting-block">
        <button className="btn-outline" onClick={() => reset()}>
          Reset to defaults
        </button>
      </div>

      {novel && (
        <div className="setting setting-block">
          <label className="setting-label">Progress</label>
          <button className="btn-outline" onClick={() => resetProgress()}>
            Reset Progress To Current
          </button>
        </div>
      )}
    </div>
  );
}
