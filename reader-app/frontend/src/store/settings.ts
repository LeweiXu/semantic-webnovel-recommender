import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "paper" | "sepia" | "night" | "black";
export type ReadingMode = "scroll" | "paginate";

interface SettingsState {
  theme: Theme;
  pinyin: boolean;
  synopsisPinyin: boolean; // pinyin + hover dictionary on the synopsis; off by default
  fontSize: number; // px
  leading: number; // line-height multiplier
  tracking: number; // letter-spacing between characters, in em
  measure: number; // column width in rem
  contrast: number; // primary text/background contrast, 50-150%; 100 is theme default
  mode: ReadingMode;
  set: (patch: Partial<SettingsState>) => void;
}

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      theme: "paper",
      pinyin: true,
      synopsisPinyin: false,
      fontSize: 20,
      leading: 2.05,
      tracking: 0.04,
      measure: 34,
      contrast: 100,
      mode: "scroll",
      set: (patch) => set(patch),
    }),
    { name: "reader:settings" },
  ),
);

// The persisted/synced fields, in a stable order, so server sync can compare
// snapshots by serialization. Excludes the `set` action.
export type StoredSettings = Pick<
  SettingsState,
  "theme" | "pinyin" | "synopsisPinyin" | "fontSize" | "leading" | "tracking" | "measure" | "contrast" | "mode"
>;

export const SETTINGS_KEYS: (keyof StoredSettings)[] = [
  "theme", "pinyin", "synopsisPinyin", "fontSize", "leading", "tracking", "measure", "contrast", "mode",
];

export function pickSettings(s: SettingsState): StoredSettings {
  return {
    theme: s.theme,
    pinyin: s.pinyin,
    synopsisPinyin: s.synopsisPinyin,
    fontSize: s.fontSize,
    leading: s.leading,
    tracking: s.tracking,
    measure: s.measure,
    contrast: s.contrast,
    mode: s.mode,
  };
}

// Apply typography + theme tokens to the document. Called from a small effect.
export function applySettings(s: SettingsState) {
  const root = document.documentElement;
  root.dataset.theme = s.theme;
  root.style.setProperty("--reading-size", `${s.fontSize}px`);
  root.style.setProperty("--reading-leading", String(s.leading));
  root.style.setProperty("--reading-tracking", `${s.tracking}em`);
  root.style.setProperty("--measure", `${s.measure}rem`);
  const contrast = Math.min(150, Math.max(50, Number(s.contrast) || 100));
  const ink = contrast <= 100
    ? `color-mix(in srgb, var(--ink-base) ${contrast}%, var(--bg))`
    : `color-mix(in srgb, var(--ink-base) ${200 - contrast}%, var(--ink-max))`;
  root.style.setProperty("--ink", ink);
}
