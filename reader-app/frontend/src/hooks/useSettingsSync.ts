import { useEffect, useRef } from "react";
import { api } from "../api/client";
import { useAuth } from "../store/auth";
import {
  SETTINGS_KEYS,
  pickSettings,
  useSettings,
  type StoredSettings,
} from "../store/settings";

// Persist settings server-side per user (alongside progress) so they follow the
// account across devices. localStorage still caches for logged-out use.
//
// Loop-safety: when we apply settings pulled from the server we pre-set
// `lastSynced` to the resulting snapshot, so the push effect sees no change and
// doesn't echo them straight back.
export function useSettingsSync() {
  const username = useAuth((s) => s.user?.username ?? null);
  const settings = useSettings();
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
        const patch: Partial<StoredSettings> = {};
        for (const key of SETTINGS_KEYS) {
          if (key in remote) (patch as Record<string, unknown>)[key] = remote[key];
        }
        if (Object.keys(patch).length === 0) return; // new account: keep local, push later
        const merged = { ...pickSettings(useSettings.getState()), ...patch };
        lastSynced.current = JSON.stringify(merged);
        useSettings.getState().set(patch);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [username]);

  // Push changes (debounced) whenever settings differ from the last synced state.
  useEffect(() => {
    if (!username) return;
    const serialized = JSON.stringify(pickSettings(settings));
    if (serialized === lastSynced.current) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      lastSynced.current = serialized;
      api.putSettings(pickSettings(useSettings.getState())).catch(() => {
        lastSynced.current = null; // allow a retry on the next change
      });
    }, 700);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [username, settings]);
}
