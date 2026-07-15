# Deploy the webnovel reader-app (personal + demo)

## Context

`reader-app/` is a working FastAPI + React reader with a Discover recommender. Today it's
single-user (one shared `data/reading_progress.json` bookmark, keyed by novel URL, shared
with the `read.py` CLI) and runs locally on one port. We want to deploy it for personal use
(read your own library with saved progress from anywhere) and as a demo, hosting the backend
on the home server (`192.168.20.9`) behind the existing Cloudflare tunnel, and the frontend on
Vercel. Along the way we add multi-user auth, in-app downloading, and an admin-only
scrape/download console.

Decisions locked with the user:
- **Server owns the library.** Seed the 9.6 GB `library/` to the server once, then it's
  authoritative; the admin console grows it on-server. `deploy.sh` syncs code only.
- **Open registration** (username + password; low blast radius — an account only stores progress).
- **Install CPU torch + bge-m3** on the server (15 GB RAM, 8 cores — plenty). Full free-text Discover.
- Auth stack mirrors the logarium app already running on this server: `passlib[bcrypt]`,
  `python-jose`, `python-multipart`. Nothing novel/risky.

Reused as-is (already built — do NOT rebuild):
- **Novel id system**: `reader-app/backend/ids.py` `nid_encode/nid_decode` = base64url of the
  landing URL (reversible). This already satisfies the "novel id" requirement; no hash needed.
- **Paste-link download**: `POST /api/download` SSE stream (`download_api.py`, `download.py` in
  client, `DownloadDialog.tsx`) already downloads a novel from a 52shuku URL with live progress.
- **Atomic JSON write pattern**: `webnovel/progress.py` `_save()` — copy this for user/job stores.
- **Detached-safe download registration**: the `_STORE_LOCK` fix in `webnovel/downloads.py`.

Server facts (verified): ports 8000 (G8) and 8001 (logarium/LOG) taken → **use 8002**. cloudflared
runs **token-based** (tunnel `1cbaa714-8ef3-4abf-a888-3e7dc29b936d`), so ingress is added in the
Cloudflare **dashboard**, not a local config file. Apps run as bare `uvicorn` processes. Python
3.12, 80 GB free. Do NOT touch G8, LOG, nginx, or the root cloudflared service.

---

## Stage 1 — Application changes (in the repo, then deploy)

### 1. Auth + per-user reading progress

New `reader-app/backend/auth.py`:
- User store `data/users.json`: `{username: {password_hash, created}}`, atomic write (copy
  `progress.py:_save`). Hash with passlib bcrypt.
- JWT via python-jose (HS256). Secret from env `NOVEL_JWT_SECRET` (fallback: generate once to
  `data/.jwt_secret`). Token returned on login, sent as `Authorization: Bearer`.
- Endpoints: `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`.
- FastAPI deps: `current_user` (decode token) and `require_admin` (username == `"lingwei"`).

New `reader-app/backend/user_progress.py`:
- Per-user progress at `data/user_progress/<username>.json`, keyed by novel URL, same shape and
  monotonic-forward rule as `webnovel/progress.py`. The CLI's shared bookmark stays independent
  (unchanged) so `read.py` is unaffected.

Modify `reader-app/backend/app.py`:
- `GET /api/library/reading` and `POST /api/novel/{nid}/progress` become per-user (require
  `current_user`, read/write that user's progress file). Keep the monotonic clamp.
- Reading a novel/chapters can stay public (browse/demo); saving progress + reading list require login.
- CORS origins from env (`NOVEL_CORS_ORIGINS`, comma-separated) including the Vercel domain +
  localhost; allow `Authorization`.

Modify `reader-app/backend/schemas.py`: add `RegisterIn`, `LoginIn`, `TokenOut`, `UserOut`.

Frontend:
- `src/api/client.ts`: add `API_BASE = import.meta.env.VITE_API_BASE ?? ""` prefixed on every
  fetch (incl. progress POST and the SSE `downloadStream`); attach `Authorization` header from
  the auth store when present.
- New `src/store/auth.ts` (zustand): token (localStorage), user, `login/register/logout`.
- New `src/components/AuthPanel.tsx`: login/register form (drawer or modal).
- `src/App.tsx`: account button in the topbar; show login state; only render the admin entry when
  `user?.username === "lingwei"`.

### 2. In-app downloads (any logged-in user)

- Paste-link flow already works — just gate `POST /api/download` behind `current_user`.
- Download a scraped-but-not-downloaded novel: in `LibraryPanel.tsx`, metadata-only search
  results are currently disabled ("Not downloaded yet"). Add a **Download** action that calls the
  existing SSE `/api/download` with `r.url`, shows progress (reuse `DownloadDialog`), then opens it.
- Same Download affordance on Discover results (`RecCard.tsx` has a `downloaded` flag).
- After a download, `novels.invalidate(url)` already refreshes caches (existing behavior).

### 3. Admin scrape/download console (user `lingwei` only)

New `reader-app/backend/admin_jobs.py`:
- Launch `scrape_metadata.py` / `download.py` / `recommend.py` as **detached** processes:
  `subprocess.Popen([venv_python, script_path, *args], start_new_session=True, stdout=logfile,
  stderr=STDOUT)`. `start_new_session=True` puts the job in its own process group so it survives
  the request, the SSE disconnect, and the browser closing.
- Input is a raw flags string the user types (e.g. `download categories gl --limit 50`). Parse
  with `shlex.split`; **allowlist** the first token to `{scrape_metadata, download, recommend}`;
  build argv from the known script path + venv python. **Never** `shell=True` → no injection.
- Registry `data/admin_jobs.json`: `{job_id: {script, args, pid, status, started, logfile,
  returncode}}`; reconcile status by checking the pid on read.
- Endpoints (all `require_admin`): `POST /api/admin/jobs` (start), `GET /api/admin/jobs` (list +
  live status), `GET /api/admin/jobs/{id}/log` (SSE tail of the logfile), `POST
  /api/admin/jobs/{id}/stop` (`os.killpg(SIGTERM)`).
- `--windscribe` is accepted but won't work on the server (no tunnel there); note in the UI.

Modify `reader-app/backend/novels.py`: in `_records()`, reload `_records_cache` when any
`metadata.jsonl` mtime changed, so novels added by a detached admin job appear without a restart.

Frontend: new `src/components/AdminPanel.tsx` (only for `lingwei`) — a command input, start
button, job list with status, live log viewer (SSE), and stop buttons.

### Tests (network-free, keep them that way)
Add to `tests/test_workflow.py`: JWT encode/decode + password hash round-trip; `admin_jobs`
argv builder accepts allowlisted scripts and rejects anything else / shell metacharacters;
per-user progress isolation + monotonic clamp.

---

## Stage 2 — Deployment

### A. One-command code sync
- New `deploy.sh` (repo root): `rsync -avz --delete --exclude-from=deploy-exclude.txt ./
  lingwei@192.168.20.9:~/Novel_Project/`.
- New `deploy-exclude.txt`: `.git/`, `library/`, `data/`, `logs/`, `**/__pycache__/`, `*.pyc`,
  `reader-app/frontend/node_modules/`, `reader-app/frontend/dist/`, `*.bak`, venvs. Keeps
  `reader-app/demo/` (needed for Discover). `--delete` won't touch excluded `library/`/`data/`.

### B. Server setup (I configure directly, per your permission; won't touch other apps)
1. `deploy.sh` → `~/Novel_Project/`.
2. `python3 -m venv ~/venv-novel`; install **CPU torch first**
   (`pip install torch --index-url https://download.pytorch.org/whl/cpu`), then `pip install -e .`,
   `pip install -r requirements.txt`, plus `passlib[bcrypt] python-jose[cryptography]
   python-multipart`. First bge-m3 query downloads ~2 GB once.
3. One-time seed: `rsync -avz ~/personal_projects/webnovels/library/ lingwei@192.168.20.9:~/Novel_Project/library/`
   (run from WSL). `data/` starts fresh (per-user progress created on the server).
4. Env: `~/Novel_Project/reader-app/backend/.env` (or systemd `Environment=`) with
   `NOVEL_JWT_SECRET`, `NOVEL_CORS_ORIGINS=https://<vercel-domain>,http://localhost:5173`.
5. Run reboot-safe **without touching root or the other apps**: a `systemd --user` unit
   `~/.config/systemd/user/novel-api.service` running `uvicorn app:app --host 0.0.0.0 --port 8002`
   from `reader-app/backend`, plus `loginctl enable-linger lingwei`. (The other apps use bare
   uvicorn; a user service is strictly additive.)
6. Verify: `curl http://localhost:8002/api/health`.

### C. Cloudflare (dashboard steps I'll write out for you)
Zero Trust → Networks → Tunnels → tunnel `1cbaa714…` → Public Hostname → Add:
`novels-api.leweixu.com` → `HTTP` → `localhost:8002`. This auto-creates the DNS CNAME. Verify
`curl https://novels-api.leweixu.com/api/health`.

### D. Vercel (dashboard steps I'll write out for you)
New project, Root Directory `reader-app/frontend`, framework Vite, build `npm run build`, output
`dist`. Env `VITE_API_BASE=https://novels-api.leweixu.com`. New `reader-app/frontend/vercel.json`
with a SPA rewrite (all routes → `/index.html`). After deploy, add the Vercel domain to
`NOVEL_CORS_ORIGINS` and restart the service.

---

## Critical files

Create: `reader-app/backend/{auth.py, user_progress.py, admin_jobs.py}`,
`reader-app/frontend/src/store/auth.ts`,
`reader-app/frontend/src/components/{AuthPanel.tsx, AdminPanel.tsx}`,
`reader-app/frontend/vercel.json`, `deploy.sh`, `deploy-exclude.txt`.
Modify: `reader-app/backend/{app.py, schemas.py, novels.py}`,
`reader-app/frontend/src/api/client.ts`,
`reader-app/frontend/src/components/{LibraryPanel.tsx, RecCard.tsx}`,
`reader-app/frontend/src/App.tsx`, `tests/test_workflow.py`,
`reader-app/backend/requirements.txt`.
Reuse (no change): `ids.py`, `download_api.py`, `download.py` client fn, `DownloadDialog.tsx`,
`webnovel/progress.py` (pattern), `webnovel/downloads.py` (`_STORE_LOCK`).

## Verification
- Local two-process dev (`uvicorn app:app --reload` + `npm run dev`): register two accounts;
  confirm progress is isolated per user and never rewinds; read a real novel; download a
  metadata-only result and a pasted link; as `lingwei` run `download categories gl --limit 1`
  in the admin console, watch the SSE log, close the tab and confirm the job keeps running, then
  Stop it.
- `~/venvs/recsys/bin/python -m unittest discover -s tests -v` stays green (adds the new tests).
- Server/prod: `curl https://novels-api.leweixu.com/api/health`; log in from the Vercel URL, read
  with progress saved, run one admin download job end-to-end.

## Open risk / note
The admin console executes real subprocesses. Safety rests on: `require_admin` (== `lingwei`),
script allowlist, `shlex.split` + `Popen` with **no shell**. No arbitrary commands, ever.
