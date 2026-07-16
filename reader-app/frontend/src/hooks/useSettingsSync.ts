import { useEffect, useRef } from "react";
import { api } from "../api/client";
import { useAuth } from "../store/auth";
import {
  SETTINGS_KEYS,
  pickProfiles,
  useSettings,
  type Profile,
  type ProfileSettings,
} from "../store/settings";

// Persist settings server-side per user (alongside progress) so they follow the
// account across devices. Both profiles (desktop + mobile) sync together.
// localStorage still caches for logged-out use.
//
// Loop-safety: when we apply settings pulled from the server we pre-set
// `lastSynced` to the resulting snapshot, so the push effect sees no change and
// doesn't echo them straight back.
export function useSettingsSync() {
  const username = useAuth((s) => s.user?.username ?? null);
  const desktop = useSettings((s) => s.desktop);
  const mobile = useSettings((s) => s.mobile);
  const lastSynced = useRef<string | null>(null);
  const timer = useRef<number | null>(null);

  // Pull the account's saved settings on login and apply them.
  useEffect(() => {
    if (!username) {
      lastSynced.current = null;
      return;
    }
    let alive = true;
    api
      .getSettings()
      .then((remote) => {
        if (!alive || !remote) return;
        const profiles: { desktop?: Partial<ProfileSettings>; mobile?: Partial<ProfileSettings> } = {};
        for (const profile of ["desktop", "mobile"] as Profile[]) {
          const blob = (remote as Record<string, unknown>)[profile];
          if (!blob || typeof blob !== "object") continue;
          const patch: Partial<ProfileSettings> = {};
          for (const key of SETTINGS_KEYS) {
            if (key in blob) (patch as Record<string, unknown>)[key] = (blob as Record<string, unknown>)[key];
          }
          if (Object.keys(patch).length) profiles[profile] = patch;
        }
        if (!profiles.desktop && !profiles.mobile) return; // new account: keep local, push later
        useSettings.getState().merge(profiles);
        lastSynced.current = JSON.stringify(pickProfiles(useSettings.getState()));
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [username]);

  // Push changes (debounced) whenever either profile differs from last synced.
  useEffect(() => {
    if (!username) return;
    const serialized = JSON.stringify(pickProfiles(useSettings.getState()));
    if (serialized === lastSynced.current) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      lastSynced.current = serialized;
      api.putSettings(pickProfiles(useSettings.getState())).catch(() => {
        lastSynced.current = null; // allow a retry on the next change
      });
    }, 700);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [username, desktop, mobile]);
}
