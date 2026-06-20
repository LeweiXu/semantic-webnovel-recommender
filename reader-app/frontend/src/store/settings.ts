import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "paper" | "sepia" | "night";
export type ReadingMode = "scroll" | "paginate";

interface SettingsState {
  theme: Theme;
  pinyin: boolean;
  fontSize: number; // px
  leading: number; // line-height multiplier
  tracking: number; // letter-spacing between characters, in em
  measure: number; // column width in rem
  mode: ReadingMode;
  set: (patch: Partial<SettingsState>) => void;
}

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      theme: "paper",
      pinyin: true,
      fontSize: 20,
      leading: 2.05,
      tracking: 0.04,
      measure: 34,
      mode: "scroll",
      set: (patch) => set(patch),
    }),
    { name: "reader:settings" },
  ),
);

// Apply typography + theme tokens to the document. Called from a small effect.
export function applySettings(s: SettingsState) {
  const root = document.documentElement;
  root.dataset.theme = s.theme;
  root.style.setProperty("--reading-size", `${s.fontSize}px`);
  root.style.setProperty("--reading-leading", String(s.leading));
  root.style.setProperty("--reading-tracking", `${s.tracking}em`);
  root.style.setProperty("--measure", `${s.measure}rem`);
}
