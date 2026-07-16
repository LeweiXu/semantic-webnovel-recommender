import { create } from "zustand";
import { api, setApiAuthToken, type User } from "../api/client";

const TOKEN_KEY = "reader:auth-token";
const USER_KEY = "reader:auth-user";

function storedToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function storedUser(): User | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as User) : null;
  } catch {
    return null;
  }
}

function persistUser(user: User | null) {
  try {
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
    else localStorage.removeItem(USER_KEY);
  } catch {
    /* storage denial shouldn't break auth */
  }
}

const initialToken = storedToken();
// Restore the last-known user synchronously so the UI doesn't flash a
// logged-out state on reload while /me is still in flight. restore() then
// validates the token and corrects this if it's stale.
const initialUser = initialToken ? storedUser() : null;
setApiAuthToken(initialToken);

interface AuthState {
  token: string | null;
  user: User | null;
  ready: boolean;
  restore: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

function persistToken(token: string | null) {
  setApiAuthToken(token);
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* Private browsing/storage denial should not prevent this session's login. */
  }
}

export const useAuth = create<AuthState>((set) => ({
  token: initialToken,
  user: initialUser,
  ready: !initialToken,

  restore: async () => {
    if (!initialToken) {
      set({ ready: true });
      return;
    }
    try {
      const user = await api.me();
      persistUser(user);
      set({ user, ready: true });
    } catch {
      persistToken(null);
      persistUser(null);
      set({ token: null, user: null, ready: true });
    }
  },

  login: async (username, password) => {
    const result = await api.login(username, password);
    persistToken(result.access_token);
    persistUser(result.user);
    set({ token: result.access_token, user: result.user, ready: true });
  },

  register: async (username, password) => {
    const result = await api.register(username, password);
    persistToken(result.access_token);
    persistUser(result.user);
    set({ token: result.access_token, user: result.user, ready: true });
  },

  logout: () => {
    persistToken(null);
    persistUser(null);
    set({ token: null, user: null, ready: true });
  },
}));
