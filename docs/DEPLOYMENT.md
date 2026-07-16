# Deployment runbook (home server + Cloudflare + Vercel)

Backend runs on the home server behind the existing Cloudflare tunnel; the frontend
runs on Vercel. This is the same shape as the LOG and G8 apps already on the server.

- Backend: `http://localhost:8002` on `192.168.20.9` -> `https://novels-api.leweixu.com`
  (via the existing cloudflared tunnel `1cbaa714-...`).
- Frontend: Vercel, pointed at the backend with `VITE_API_BASE`.

Ports 8000 (G8) and 8001 (LOG) were already taken, so this app uses **8002**.

---

## What is already set up on the server

Done during initial deploy (you don't need to redo these):

- `~/Novel_Project/` — the whole repo, synced by `deploy.sh` (code only).
- `~/Novel_Project/library/` — the 9.6 GB library (9,754 novels) seeded once from WSL.
- `~/venv-novel/` — Python venv with CPU torch, the project (`pip install -e .`), and the
  auth deps (passlib/bcrypt, python-jose, python-multipart).
- `~/novel-api.env` — holds `NOVEL_JWT_SECRET` and `NOVEL_CORS_ORIGINS`. Lives outside
  `~/Novel_Project/` on purpose, so `deploy.sh --delete` never wipes it. `chmod 600`.
- `~/.config/systemd/user/novel-api.service` — runs uvicorn on 8002, `Restart=on-failure`,
  enabled at boot (user lingering is on, so it survives reboots without a login session).
- The other apps, nginx, and the root cloudflared service were not touched.

Service controls (all sudo-free, as user lingwei):

```bash
systemctl --user status novel-api
systemctl --user restart novel-api
journalctl --user -u novel-api -f          # live logs
```

---

## 1. Cloudflare: expose the backend

The tunnel is **token-based**, so ingress is edited in the dashboard (there is no local
config file to change).

1. Cloudflare dashboard -> **Zero Trust** -> **Networks** -> **Tunnels**.
2. Open the existing tunnel (ID `1cbaa714-8ef3-4abf-a888-3e7dc29b936d`, the one already
   serving api/g8-api).
3. **Public Hostname** tab -> **Add a public hostname**:
   - Subdomain: `novels-api`
   - Domain: `leweixu.com`
   - Path: (empty)
   - Type: `HTTP`
   - URL: `localhost:8002`
4. Save. This auto-creates the `novels-api` CNAME in the `leweixu.com` DNS zone (proxied),
   same as the existing api/g8-api records. No manual DNS entry needed.

Verify from anywhere:

```bash
curl https://novels-api.leweixu.com/api/health
# {"ok":true,"dictionary":true}
```

---

## 2. Vercel: deploy the frontend

1. New Vercel project from this Git repo (or `vercel` CLI from `reader-app/frontend`).
2. **Root Directory**: `reader-app/frontend`.
3. Framework preset: **Vite** (build `npm run build`, output `dist` — already the defaults).
   SPA routing is handled by `reader-app/frontend/vercel.json`.
4. **Environment Variable**:
   - `VITE_API_BASE = https://novels-api.leweixu.com`
   (Vite inlines this at build time, so redeploy after changing it.)
5. Deploy. Note the production domain, e.g. `https://<project>.vercel.app`.

---

## 3. Wire CORS + create the admin account

The browser calls the backend cross-origin (Vercel domain -> leweixu.com), so the backend
must allow that origin.

On the server, put your real Vercel domain into the env file and restart:

```bash
ssh lingwei@192.168.20.9
# edit ~/novel-api.env  ->  NOVEL_CORS_ORIGINS=https://<project>.vercel.app
# (comma-separate to allow more than one origin; localhost:5173 is always allowed)
systemctl --user restart novel-api
```

Then, from the deployed site, **register the username `lingwei` first** — that account is
the admin (it's the only one that can see the scrape/download console). Do this before
sharing the URL, since registration is open and whoever claims `lingwei` first is admin.

---

## Everyday operations

**Push code changes** (from the WSL repo):

```bash
./deploy.sh                         # rsync code only; library/ and data/ untouched
ssh lingwei@192.168.20.9 'systemctl --user restart novel-api'
```

**Grow the library** (admin only): log in as `lingwei`, open the admin console, and type a
command, e.g. `download categories gl --limit 50` or
`scrape_metadata --category gl --recommendation-depth 0`. Jobs run detached on the server
(closing the tab doesn't stop them); use Stop to cancel. `--windscribe` won't work there
(no tunnel on the server), so leave it off.

**Frontend changes**: push to Git; Vercel redeploys. If you changed `VITE_API_BASE`,
trigger a fresh deploy so it gets re-inlined.

## Notes / gotchas

- Free-text Discover uses bge-m3 on CPU. The first query after a restart downloads/loads the
  model (~2 GB, cached under `~/.cache/huggingface` afterward) and takes a few seconds;
  Similar/map/tags need no model and are instant.
- `data/` on the server (users, per-user progress, JWT secret, admin job registry) is created
  at runtime and excluded from `deploy.sh`, so it persists across deploys. Back it up if you
  care about accounts/progress.
- Password changes: there's no self-serve change-password endpoint yet. To reset an account,
  edit `~/Novel_Project/data/users.json` (remove the user) and re-register.
