# Handoff

Snapshot of a big session of reader-app work: mobile support, a novel view page,
readable URLs, a pile of UI fixes, and reader scroll smoothing. Written 2026-07-17.

## Read this first: deploy coupling

Everything below is committed and pushed to `origin/main` (`webnovel-scraper` on
GitHub). Two halves of the app deploy separately and this session changed both in
ways that depend on each other, so they must ship together:

- **Frontend** (Vercel, builds from this repo on push) probably already
  auto-deployed the new code.
- **Backend** (home server, `~/Novel_Project`) only updates when you run
  `./deploy.sh`. It has NOT been deployed this session.

If the frontend is live but the backend is old, the site breaks:

- The frontend now calls `/api/novel/<category>/<stem>` (slug URLs). The old
  backend only knows `/api/novel/{nid}` and can't match a path with a slash, so
  every novel/reader page 404s.
- Settings now sync as `{desktop, mobile}`; the old backend stores a flat blob
  and would drop them.

**Action: run `./deploy.sh` to bring the server up to `66008f6`.** It now also
restarts the service for you (see below). After that, frontend and backend agree.

## State

- All work is on `main`, tree clean, in sync with `origin/main`.
- Frontend builds clean (`cd reader-app/frontend && npm run build`).
- Backend tests pass (`~/venvs/recsys/bin/python -m unittest discover -s tests`),
  26 tests.
- Not yet verified on a real phone (scroll feel + mobile viewport). See open items.

## Server changes made live this session (not in git)

These were edited straight on the server, separate from the code in the repo:

- **CORS fix.** `~/novel-api.env` had `NOVEL_CORS_ORIGINS=novel-reader-recommender.vercel.app`
  with no scheme, so the browser's `https://…` origin never matched and every
  cross-origin request 400'd. Fixed to `https://novel-reader-recommender.vercel.app`,
  backup at `~/novel-api.env.bak.<ts>`, service restarted.
- **Reset the `lingwei` account.** Removed it from `~/Novel_Project/data/users.json`
  (backup alongside) so it could be re-registered after a password typo. Register
  `lingwei` first on the deployed site to reclaim admin.

## What changed, by area

### Mobile support (breakpoint 1024px)
- `hooks/useIsMobile.ts` (`MOBILE_MAX_WIDTH = 1024`) drives JS-side behaviour;
  `styles/responsive.css` holds the CSS media queries at the same 1024px. Keep the
  two in sync.
- Reader header on mobile shows only Contents (left) and Settings (right); the
  title stays and links to the novel page (the way out on mobile). Full header on
  every other view.
- Library cards drop the synopsis on mobile; novel page goes full-width with a
  single-column chapter list.

### Separate desktop/mobile settings
- Backend `user_settings.py` stores `{ "desktop": {...}, "mobile": {...} }`,
  allowlisted per profile, partial-merge on PUT, and reads legacy flat files as
  the desktop profile. GET/PUT `/api/settings` carry the nested shape.
- Frontend `store/settings.ts` holds both profiles + an active `profile`;
  `App` sets the active one from `useIsMobile()`. `set()` patches the active
  profile, `reset()` restores that profile's defaults. localStorage migrates the
  old flat blob (persist version 2). `useSettingsSync` syncs both profiles.
- Desktop defaults: 24px / 70rem / 1.6 leading. Mobile has its own tuned defaults.
  Line-spacing slider is 1.0–3.0.

### Novel view page (`/novel/<id>`)
- `components/NovelPage.tsx`: title, tags, synopsis, per-chapter TOC. Statically
  designed to match Discover/Library (own typography, UI font), deliberately NOT
  driven by the reader's font/pinyin settings.
- Synopsis truncates at `SYNOPSIS_CAP` (currently 250 chars) with View full /
  Show less. 2/3 width centered on desktop, full width on mobile; 3-column
  horizontal chapter grid on desktop, single column on mobile.
- Reachable from the Library (all four open spots go here), from the reader title,
  and by direct URL. `tags` was added to `NovelDetail`.

### Readable file-path URLs (slug)
- Browser URLs are `/novel/<category>/<stem>` and `/reader/<category>/<stem>`
  (`.txt` dropped), e.g. `/reader/gl/师姐请息怒_柄炳爱吃饼_完结+番外`.
- Backend derives the slug from `record.file` (`novels.slug_for`), builds a
  cached `slug -> url` index, and `_resolve_or_404` accepts a slug OR a legacy
  base64 id (slugs contain a `/`, base64 ids never do). Old shared links still
  work. Reader/novel/chapter/progress routes use `{nid:path}`, with chapter and
  progress declared before the catch-all detail route (the greedy converter would
  otherwise swallow the suffixes).
- `slug` is on `NovelDetail`, `ReadingItem`, `SearchItem`, rec results, and the
  download SSE `done` event. Frontend routing/store use the slug and encode each
  path segment while keeping the slash.

### Reader scroll smoothness
- Header hide no longer animates `margin-bottom` (a layout prop that reflowed the
  scroll container every frame). In the reader the header is now an overlay
  (`.app.reading .topbar { position: absolute }`) and hides via `transform` only.
  The scroll column gets a static 56px top pad; `ScrollReader`'s
  `READING_TOP_INSET` keeps exact-resume landing your line below the header.
- `.app` height is `100dvh` (with `100vh` fallback) so the reading area tracks the
  mobile browser's collapsing toolbars instead of leaving a gap. `html`/`body`
  are painted with the theme colour so any revealed strip matches the theme.
- Footer now renders only on Discover.

### Smaller fixes
- Word-hover highlight uses a `--word-hover` token; dark/black themes override it
  to a light wash so it's visible on near-black.
- Library shelf is cached per-user in localStorage (stale-while-revalidate) so it
  paints instantly instead of waiting ~1s on the backend.
- Auth persists the last user optimistically, so reload doesn't flash a
  logged-out state; the Library empty state is gated on the auth `ready` flag.
- Discover tag chips are hardcoded (the demo corpus's real top-16 tags) so they
  render instantly; the semantic map frame renders immediately and points fade in.

### deploy.sh
- Now runs `systemctl --user restart novel-api` over SSH after the rsync and
  reports `is-active`, so a deploy is one command. `Restart=on-failure` in the
  unit still covers a bad import.

## How to deploy

- **Backend:** `./deploy.sh` (rsyncs code, restarts the service, prints
  `is-active`). `library/` and `data/` on the server are excluded and untouched.
- **Frontend:** push to `main`; Vercel rebuilds. If you touched `VITE_API_BASE`,
  trigger a fresh deploy so it re-inlines.

## Open items / not done

- **Verify on a real phone.** The scroll-smoothness and `dvh` changes are feel/
  viewport things that couldn't be tested in the dev sandbox. If the header hide
  still stutters or a bottom gap remains, options include swapping `dvh` for
  `svh` or tuning the transition.
- **Scale embeddings to the whole `library/`** (asked about, not built). The
  library has 57,285 metadata records; embeddings at 1024×4 bytes/record is ~234
  MB, and serving is a pure NumPy matmul (no GPU). Plan: precompute on the 5070
  (`python recommend.py update`, CUDA 12.8 torch), rsync `data/rec_index/` to the
  server, and point Discover at it. `recommend_api` currently hardcodes
  `reader-app/demo`; add a `NOVEL_REC_DIR` env override so the server can use the
  full index while the committed 500-record demo stays the default for the public
  build. Two caveats: free-text query still needs bge-m3 on the server (CPU is
  fine, ~2 GB RAM, slow first query, model-free browse/Similar/map stay instant),
  and the semantic map can't render 57k DOM dots so it needs sampling.
- **`reader-app/README.md`** still documents the old flat settings storage and the
  pre-slug page list. Worth updating.

## Gotchas

- Novel ids are dual: slug for downloaded novels (reader/library flows), base64
  for Discover references (which include non-downloaded corpus novels). The
  backend resolver takes either; the frontend prefers `slug` and falls back to
  `nid`.
- The reader shares one bookmark and only advances forward. Exact-resume math in
  `ScrollReader` assumes the 56px header overlay (`READING_TOP_INSET`); if the
  header height changes, update both the CSS pad and that constant.
- Tests are network-free by design. Keep them that way.
