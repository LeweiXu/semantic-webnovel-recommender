import { useSettings, type Theme, type ReadingMode } from "../store/settings";
import { useReader } from "../store/reader";

const THEMES: { id: Theme; label: string }[] = [
  { id: "paper", label: "Paper" },
  { id: "sepia", label: "Sepia" },
  { id: "night", label: "Night" },
  { id: "black", label: "Black" },
];

export function SettingsPanel() {
  const s = useSettings();
  const novel = useReader((st) => st.novel);
  const topChapter = useReader((st) => st.topChapter);
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
          onClick={() => s.set({ pinyin: !s.pinyin })}
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
          onClick={() => s.set({ synopsisPinyin: !s.synopsisPinyin })}
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
              onClick={() => s.set({ theme: t.id })}
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
          onChange={(e) => s.set({ fontSize: Number(e.target.value) })}
        />
      </div>

      <div className="setting setting-block">
        <label className="setting-label">
          Line spacing <span className="setting-value">{s.leading.toFixed(2)}</span>
        </label>
        <input
          type="range"
          min={1.6}
          max={2.6}
          step={0.05}
          value={s.leading}
          onChange={(e) => s.set({ leading: Number(e.target.value) })}
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
          onChange={(e) => s.set({ tracking: Number(e.target.value) })}
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
          onChange={(e) => s.set({ measure: Number(e.target.value) })}
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
          onChange={(e) => s.set({ contrast: Number(e.target.value) })}
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
              onClick={() => s.set({ mode: m })}
            >
              {m === "scroll" ? "Scroll" : "Paginate"}
              {m === "paginate" && <em className="soon">soon</em>}
            </button>
          ))}
        </div>
      </div>

      {novel && (
        <div className="setting setting-block">
          <label className="setting-label">Progress</label>
          <button className="btn-outline" onClick={() => resetProgress()}>
            Reset progress to here (ch {topChapter + 1})
          </button>
          <p className="setting-hint">
            Moves your saved place back to the top of the current page — handy after
            an accidental jump to a later chapter.
          </p>
        </div>
      )}
    </div>
  );
}
