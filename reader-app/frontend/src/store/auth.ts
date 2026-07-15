import { create } from "zustand";
import { api, setApiAuthToken, type User } from "../api/client";

const TOKEN_KEY = "reader:auth-token";

function storedToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

const initialToken = storedToken();
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
  user: null,
  ready: !initialToken,

  restore: async () => {
    if (!initialToken) {
      set({ ready: true });
      return;
    }
    try {
      const user = await api.me();
      set({ user, ready: true });
    } catch {
      persistToken(null);
      set({ token: null, user: null, ready: true });
    }
  },

  login: async (username, password) => {
    const result = await api.login(username, password);
    persistToken(result.access_token);
    set({ token: result.access_token, user: result.user, ready: true });
  },

  register: async (username, password) => {
    const result = await api.register(username, password);
    persistToken(result.access_token);
    set({ token: result.access_token, user: result.user, ready: true });
  },

  logout: () => {
    persistToken(null);
    set({ token: null, user: null, ready: true });
  },
}));
