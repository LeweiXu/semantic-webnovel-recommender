import { useState, type FormEvent } from "react";
import { useAuth } from "../store/auth";
import { useReader } from "../store/reader";
import { discoverPath, navigate } from "../routing";

interface Props {
  onClose: () => void;
}

export function AuthPanel({ onClose }: Props) {
  const user = useAuth((s) => s.user);
  const login = useAuth((s) => s.login);
  const register = useAuth((s) => s.register);
  const logout = useAuth((s) => s.logout);
  const closeNovel = useReader((s) => s.closeNovel);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(username, password);
      else await register(username, password);
      closeNovel();
      navigate(discoverPath());
      onClose();
    } catch (e: any) {
      setError(e?.message ?? "Authentication failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-layer" role="dialog" aria-modal="true" aria-label="Account">
      <button className="modal-scrim" onClick={onClose} aria-label="Close" />
      <section className="modal-card auth-card">
        <div className="modal-head">
          <div>
            <div className="section-label">Account</div>
            <h2 className="modal-title">{user ? user.username : "Save your place"}</h2>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        {user ? (
          <div className="auth-signed-in">
            <p>Your reading list and chapter progress are saved to this account.</p>
            <button
              className="btn-seal"
              onClick={() => {
                logout();
                closeNovel();
                navigate(discoverPath());
                onClose();
              }}
            >
              Log out
            </button>
          </div>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            <div className="auth-tabs">
              <button type="button" className={mode === "login" ? "is-on" : ""} onClick={() => setMode("login")}>Log in</button>
              <button type="button" className={mode === "register" ? "is-on" : ""} onClick={() => setMode("register")}>Register</button>
            </div>
            <label>
              Username
              <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" minLength={3} required />
            </label>
            <label>
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={8} required />
            </label>
            {error && <div className="form-error">{error}</div>}
            <button className="btn-seal" disabled={busy}>
              {busy ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
            </button>
          </form>
        )}
      </section>
    </div>
  );
}
