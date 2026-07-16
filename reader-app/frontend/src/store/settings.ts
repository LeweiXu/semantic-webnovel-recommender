import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "paper" | "sepia" | "night" | "black";
export type ReadingMode = "scroll" | "paginate";
export type Profile = "desktop" | "mobile";

// The reader settings, held independently per profile so a phone/tablet keeps
// its own type size, column width, etc. from the desktop.
export interface ProfileSettings {
  theme: Theme;
  pinyin: boolean;
  synopsisPinyin: boolean; // pinyin + hover dictionary on the synopsis; off by default
  fontSize: number; // px
  leading: number; // line-height multiplier
  tracking: number; // letter-spacing between characters, in em
  measure: number; // column width in rem
  contrast: number; // primary text/background contrast, 50-150%; 100 is theme default
  mode: ReadingMode;
}

export const SETTINGS_KEYS: (keyof ProfileSettings)[] = [
  "theme", "pinyin", "synopsisPinyin", "fontSize", "leading", "tracking", "measure", "contrast", "mode",
];

const DESKTOP_DEFAULTS: ProfileSettings = {
  theme: "paper",
  pinyin: true,
  synopsisPinyin: false,
  fontSize: 24,
  leading: 1.6,
  tracking: 0.04,
  measure: 70,
  contrast: 100,
  mode: "scroll",
};

// Tuned for phones out of the box: a touch smaller and tighter than desktop.
const MOBILE_DEFAULTS: ProfileSettings = {
  theme: "paper",
  pinyin: true,
  synopsisPinyin: false,
  fontSize: 18,
  leading: 1.6,
  tracking: 0.02,
  measure: 30,
  contrast: 100,
  mode: "scroll",
};

export const PROFILE_DEFAULTS: Record<Profile, ProfileSettings> = {
  desktop: DESKTOP_DEFAULTS,
  mobile: MOBILE_DEFAULTS,
};

interface SettingsState {
  desktop: ProfileSettings;
  mobile: ProfileSettings;
  profile: Profile; // which set is active; App drives this from useIsMobile()
  setProfile: (p: Profile) => void;
  set: (patch: Partial<ProfileSettings>) => void; // patches the ACTIVE profile
  reset: () => void; // reset the ACTIVE profile to its defaults
  // Merge server-pulled values into either profile (used by settings sync).
  merge: (profiles: { desktop?: Partial<ProfileSettings>; mobile?: Partial<ProfileSettings> }) => void;
}

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      desktop: { ...DESKTOP_DEFAULTS },
      mobile: { ...MOBILE_DEFAULTS },
      profile: "desktop",
      setProfile: (p) => set((s) => (s.profile === p ? {} : { profile: p })),
      set: (patch) =>
        set((s) => ({ [s.profile]: { ...s[s.profile], ...patch } }) as Partial<SettingsState>),
      reset: () =>
        set((s) => ({ [s.profile]: { ...PROFILE_DEFAULTS[s.profile] } }) as Partial<SettingsState>),
      merge: (profiles) =>
        set((s) => ({
          desktop: { ...s.desktop, ...(profiles.desktop ?? {}) },
          mobile: { ...s.mobile, ...(profiles.mobile ?? {}) },
        })),
    }),
    {
      name: "reader:settings",
      version: 2,
      partialize: (s) => ({ desktop: s.desktop, mobile: s.mobile, profile: s.profile }),
      // v0/v1 stored a single flat settings blob. Carry it into the desktop
      // profile; mobile starts from its own defaults.
      migrate: (persisted: any, version: number) => {
        if (!persisted || version >= 2) return persisted;
        const flat: Partial<ProfileSettings> = {};
        for (const key of SETTINGS_KEYS) {
          if (persisted[key] !== undefined) (flat as any)[key] = persisted[key];
        }
        return {
          desktop: { ...DESKTOP_DEFAULTS, ...flat },
          mobile: { ...MOBILE_DEFAULTS },
          profile: "desktop",
        };
      },
    },
  ),
);

export type ProfilesPayload = { desktop: ProfileSettings; mobile: ProfileSettings };

// The two profiles, for server sync.
export function pickProfiles(s: SettingsState): ProfilesPayload {
  return { desktop: s.desktop, mobile: s.mobile };
}

// The settings currently in effect (active profile). Subscribe with this so a
// component re-reads when either the profile switches or its values change.
export function useActiveSettings(): ProfileSettings {
  return useSettings((s) => s[s.profile]);
}

// Apply typography + theme tokens to the document. Called from a small effect.
export function applySettings(s: ProfileSettings) {
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
