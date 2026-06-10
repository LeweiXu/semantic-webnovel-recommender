# Project Context — 52shuku.net GL Novel Scraper

> Briefing for an agentic coding assistant. Read this fully before changing
> code. It records the objective, the confirmed site structure, the tooling we
> built, hard-won gotchas, and — critically — **approaches that do NOT work**, so
> they aren't re-attempted.

## Objective

Scrape **all GL (百合) novels** from `https://www.52shuku.net/` for personal
offline reading.

- **Built & working:** `.txt` output (one file per novel, with a metadata
  preamble + chapter-segmented body).
- **Not yet built (future):** `.epub` and `.md` serialisers. A common in-memory
  novel representation already exists (see `ScrapedNovel` / `NovelMeta`) to build
  these on later.

Stack: **Python**, `curl_cffi` (browser-impersonating HTTP, NOT `requests`),
`BeautifulSoup` + `lxml`. Encoding is **UTF-8** throughout (no GB18030 dance).

---

## What we've built (file inventory)

| File | Purpose |
|------|---------|
| `scraper.py` | Shared scraper library: HTTP fetching, page parsing, text extraction, output formatting, state handling, and run logging. It has no CLI. |
| `scrape_from_walk.py` | Previous/next chain-walk CLI. Provides seed, forward, backward, resume, one-off download, and repair modes. |
| `scrape_from_catalogue.py` | Downloads live URLs from `data/gl_catalog.json`. Default: one direct newest→oldest worker. `--windscribe` adds a VPN-proxied oldest→newest worker using a collision-free shared queue. It derives resume state only from complete output files and never touches `state.json`. |
| `scripts/create_catalogue.py` | **Catalogue builder / discovery.** Graph-crawls the whole GL catalogue into `data/gl_catalog.json` and writes its report under `reports/`. |
| `scripts/analyze_catalogue_chains.py` | Analyses `data/gl_catalog.json`, writing its visual report under `reports/` and structured JSON under `data/`. |
| `scripts/inspect_urls.py` | Scans `output/**/*.txt` preambles and writes `data/url_map.json`. |
| `scripts/report_size.py` | Disk-usage summary of `output/` (total, avg, per-month). |
| `scripts/check_incomplete.py` | Scans output for `[页面获取失败]` placeholders and lists novels needing repair. |
| `scripts/probe_rate_limit.py` | One-shot rate-limit characterisation. Already run. |
| `scripts/configure_windscribe.py` | One-time Windscribe CLI-only configuration: enables its HTTP proxy gateway and excludes the active Python executable from the VPN tunnel. |
| `state.json` | Resumable state (atomic-written + `.bak`): `oldest_url`, `newest_url`, `scraped[]`. |
| `data/gl_catalog.json` | Canonical novel list from `scripts/create_catalogue.py` (oldest-first). |
| `data/url_map.json` | Per-downloaded-novel URL data from `scripts/inspect_urls.py`. |
| `docs/robots.txt`, `docs/sitemap.xml` | Captured site metadata. |
| `reports/` | Generated human-readable reports. |

### `scrape_from_walk.py` modes
- `--seed URL` — scrape one novel, initialise `state.json`.
- `--backward` — walk `上一篇` from `oldest_url` (the main bulk direction: newest→oldest).
- `--forward` — walk `下一篇` from `newest_url` (catch novels newer than the seed).
- `--resume URL` — with `--forward/--backward`: start the walk AT this URL (inclusive). Manual bridge across an unrecoverable break.
- `--get URL` — download a single novel into `--output` (flat, no month dir, overwrites). No state change. For one-offs / re-downloads.
- `--repair` — re-download every novel under `--output` that has failed-page placeholders.
- Common flags: `--limit N`, `--output DIR`, `--workers N` (parallel page fetch; default 1), `--verbose` (per-page log on its own line vs in-place).

---

## Site structure (confirmed)

### URL shapes — there are THREE schemes across history
The chain/crawl handles all of them transparently because each page hands us the
literal prev/next/recommendation URLs.

- **Modern (~2024 →):** `/gl/{DD}_b/{base62}.html` e.g. `/gl/07_b/bkecS.html`.
  **`{DD}_b` is the upload DAY-OF-MONTH**, reused every month (so `/gl/04_b/`
  holds everything uploaded on the 4th of *any* month). This is NOT a hash bucket.
- **Mid (~2020–2023):** `/gl/b/{base62}.html` e.g. `/gl/b/bjPxp.html`. Single
  shard letter `b`; all novels of the era share it.
- **Old (~2018–2020):** `/gl/{base62}.html` single-level e.g. `/gl/hqNo.html`,
  and oldest numeric `/gl/12764.html`.

### IDs
`{base62}` uses alphabet **`0-9A-Za-z`** (verified: `bkdXU-1 = bkdXT`,
`bkdXU+3 = bkdXX`, matching real prev/next links). The counter is **global
across all categories** (gl/bl/yanqing/…), so consecutive GL IDs are
**non-contiguous** — gaps of +2…+100+ are other categories' novels. Decoding an
ID to an int is only useful for local stepping, not for counting GL novels.

### Landing page selectors (`parse_landing`)
- Title+author+status: `h1.article-title` → text like `冷淡学霸与可爱小猫_宋叙彦【完结】`.
  Parse: strip `【…】` (status), then split on the first `_` → (title, author).
- Upload date: `time.muted` (e.g. `2026年06月06日 16:26:16`).
- Chapter/reading pages: `ul.list li.mulu a` → `{id}_2.html`, `_3.html`, …
  (the "开始阅读" link duplicates `_2`; de-dup by href). **Do not assume pages
  start at `_2`** — read the listed hrefs.
- Prev/next: `nav.article-nav span.article-nav-prev a` / `span.article-nav-next a`
  (absolute hrefs). 下一篇 on the newest novel points to `index.html` (stop signal).
- **Recommendations:** `div.relates a[href]` (≈10 links to OTHER GL novels in
  other shards/days). `div.related_posts` also contains `/zuozhe/` author links —
  filter to `/gl/` landing pages only. **These are the key to crossing gaps**
  (see Discovery).

### Chapter ("page") structure (`parse_chapter_page`)
- Text container: `article.article-content` (also `id="nr1"`); story is `<p>` tags.
- A "page" (`_N.html`) is a fixed word-count slice, **not** a chapter. Real
  chapters are marked by paragraphs matching `第[\d一二三…]+章` (`CHAPTER_RE`),
  which can appear anywhere mid-page. We concatenate all pages, then split on
  those markers; the `═`×40 divider is inserted at each `第N章`.
  - Some novels have **no** `第N章` markers (logged as `0章`) → saved as one block.
- **Ad/promo injection** lives in plain `<p>` tags with no special class, usually
  at the page end: "哦豁…记得收藏网址 https://www.52shuku.net/…", "传送门：…".
  Stripped by substring match against `AD_PATTERNS`
  (`52书库`, `52shuku`, `传送门：`, `记得收藏网址`, `推荐给朋友`, `如果觉得52`).

---

## Discovery strategy — what works

### 1. Chain walk (`scrape_from_walk.py --backward/--forward`)
Follow `上一篇`/`下一篇` links, scraping each novel as reached. Reliable **within
a contiguous segment**. `state.json["scraped"]` is a set used as a **loop guard**
(the ~Nov-2020 origin cluster's prev-links form a cycle; revisiting a scraped URL
terminates the walk cleanly).

### 2. Same-shard bridge (deletion gaps)
When a `上一篇/下一篇` 404s (novel deleted, neighbours not relinked), probe
`PROBE_BUDGET=150` id-steps **within the same shard prefix** to find the next
live novel, then **verify** its reverse link points back into the probed gap
before auto-continuing. Works in the **mid `/gl/b/` era** (one shared shard).

### 3. Graph crawl (`scripts/create_catalogue.py`) — the robust full-catalogue method
The chain alone **cannot cross a deletion gap in the modern `/gl/DD_b/` scheme**:
the shard is the upload *day*, so the chronological predecessor lives in a
*different* shard and an in-shard probe can never reach it. Solution: BFS the
catalogue graph using **prev + next + recommendation links** as edges.
- prev/next walk each contiguous segment both directions.
- recommendations (`div.relates`, which point to other shards/days) **hop across
  gaps** to disconnected segments. The rec pool snowballs (~+7/page).
- A small same-shard bridge still crosses tiny same-day gaps.
- Seedable/resumable via `--seed-map data/url_map.json | data/gl_catalog.json`; a catalogue
  seed has no stored recs, so it **primes** the pool by harvesting recs from the
  N oldest known novels (`--prime`).
- Reports **missing segments**: chain breaks (a novel whose `上一篇` isn't in the
  set) and date-coverage gaps (>N empty days).

The crawl exhausts all known `上一篇`/`下一篇` chain frontiers before using
recommendations. Recommendations are explored breadth-first from a root, with
the default maximum depth set by `RECOMMENDATION_BFS_DEPTH` near the top of
`scripts/create_catalogue.py` (overridable with `--recommendation-depth`).

On resume from `data/gl_catalog.json`, startup uses the same reciprocal-chain
partition as `scripts/analyze_catalogue_chains.py`. It requests only URLs marked
`missing_from_catalogue`, then refreshes the newest live chain end once to
discover uploads added since the previous run. Existing confirmed-404 entries
are never requested. If a newly confirmed 404 is referenced by an existing
older and/or newer live record, that boundary is considered represented and no
ID probing is attempted. Same-shard ID probing is disabled by default
(`--bridge-steps 0`) and must be explicitly enabled.

`data/gl_catalog.json` persists request outcomes:
- `fetch_status: "ok"` — live landing page.
- `fetch_status: "not_found"` — confirmed 404; title/author/date/navigation are
  `null`. Both `scripts/create_catalogue.py` and `scrape_from_catalogue.py` skip these
  URLs on later runs, preventing repeated 404 probes.
- Live records harvested by the updated crawler also store
  `recommendation_urls`, `recommendation_depth`, and
  `recommendations_crawled`, allowing recommendation BFS to resume.

The history report treats the JSON catalogue as authoritative: every non-null
`prev_url` must be represented by either a live or confirmed-404 entry. Date
gaps connected by a known chain path are suppressed because some old pages have
upload dates inconsistent with their prev/next position.

---

## What does NOT work (do not re-attempt)

1. **Paginated index `/gl/index_N.html`.** Curated recent list only; bottoms out
   ~2025. ❌ Incomplete.
2. **`docs/sitemap.xml`.** Rolling "recently updated" feed (~491 URLs site-wide, ~50
   GL, all recent). `sitemap_index.xml` 404s. ❌ Incomplete.
3. **`/so/` search.** Disallowed by `docs/robots.txt`; query strings (`/*?*`) blocked too. ❌ Off-limits.
4. **Exhaustive base62 sweep.** Global IDs → mostly other-category 404s (looks
   like abuse) and misses old schemes. ❌ Impolite + incomplete.
5. **In-shard id-bridge across a MODERN-scheme gap.** Because the shard is the
   upload day, decrementing the id within one `/gl/DD_b/` shard only finds novels
   from the *same day* — it can never reach a predecessor uploaded on another day.
   Use **recommendations** (graph crawl) to cross modern-era gaps. ❌ Structural dead-end.

---

## Anti-bot, throttling, politeness

### Cloudflare classification (`_classify`)
Sniff the **body**, not just the status code.
- **`__CF$cv$params` and `challenge-platform` are injected into EVERY real page**
  (standard CF JS fingerprinting) — they are NOT block signals. Do not treat them as challenges.
- Real content markers (`小说简介`, `上一篇`, `下一篇`, `article-content`) → `OK`.
- Hard-block markers (only on interstitial pages): `Just a moment`,
  `cf-browser-verification`, `Attention Required` → `CHALLENGED`.
- HTTP 429 → `RATE_LIMITED`. An exception/no-response (reset/timeout) → `ERROR`
  (a *harder* throttle than 429: the server hung up). Genuine 404 → `NOT_FOUND`.

### Transport: `curl_cffi` with browser impersonation
Plain `requests` gets a CF JS challenge injected and trips detection. `curl_cffi`
with `impersonate=IMPERSONATE` (TLS/HTTP2 fingerprint) sails through.
- **GOTCHA:** `IMPERSONATE` must be a target your installed `curl_cffi` supports.
  Dev env had 0.15 (`chrome136`); the runtime venv (`LOG-venv`) has **0.7.4**,
  whose newest is **`chrome124`**. Mismatch → instant `ImpersonateError` on every
  request (looks like a total failure). Current value: **`chrome124`**.

### Measured throttle & observed behaviour
Probe result: clean down to 0.5s delay; **bursts of ~80 requests fine**; the site
tolerates ~10 req/s for minutes. In practice the scraper uses
`DELAY_CHAPTER=0.1s` (within a novel) and `DELAY_NOVEL=2.0s` (between novels),
both jittered.
- On very long novels (200–600 pages) or under concurrent load you'll
  occasionally see `RATE_LIMITED` then `ERROR`. **A single 5s backoff has always
  been enough to recover** — we've never needed more than one retry. `BACKOFF_BASE=5s`,
  `BACKOFF_MAX=60s`, `max_retries=4`.
- Be a good citizen: single-threaded by default, jittered delays, checkpoint
  after every novel.

### Windscribe dual-route catalogue mode

The verified Ubuntu AMD64 CLI-only package is Windscribe `2.22.10`:

```bash
sudo apt-get install /tmp/windscribe-cli.deb
windscribe-cli login
python3 scripts/configure_windscribe.py
python3 scrape_from_catalogue.py --windscribe
```

`scripts/configure_windscribe.py` enables an HTTP proxy gateway at
`127.0.0.1:8888` and configures split tunnelling to exclude the current Python
executable. The direct session therefore keeps the normal public IP, while the
second session explicitly uses the Windscribe proxy. Startup compares both
public IPs and refuses to run if they are the same.

In dual mode, the direct worker claims pending URLs newest→oldest and the
Windscribe worker claims oldest→newest. A locked deque prevents duplicate
claims. `--workers N` applies per route, so `--windscribe --workers 3` can run
up to three chapter requests on each IP concurrently.

### `docs/robots.txt`
- `/gl/` is **allowed**. Honor disallows: `/e/*`, `/*?*`, `/d/*`, `/so/*`,
  `/templets`, `/404.html`, `/bookcase.html`, `/skin/52shuku/js/*`.
- Named AI/SEO bots (ClaudeBot, GPTBot, …) are `Disallow: /`. Use an honest
  generic browser UA; do NOT impersonate those bots or Googlebot.
- `Content-Signal: ai-train=no` is declared. This is **personal offline reading,
  not AI training** — keep impact minimal.

---

## Output format & data model

`output/{YYYY-MM}/{title}_{author}_{status}.txt` — month dir is from the novel's
**upload** date. Preamble then `═`×40-separated chapters:

```
标题：冷淡学霸与可爱小猫
作者：宋叙彦
状态：完结
上传时间：2026年06月06日 16:26:16
章节数：11
完整性：完整                ← or "缺失N页（见正文 [页面获取失败]）"
抓取时间：2026-06-07T15:41:22Z

上一篇：<title>
        URL:  <url>
        文件: <filename.txt>
下一篇：<title>
        URL:  <url>
        文件: <filename.txt>
来源：<this novel's URL>

════════════…
第1章
<text>
════════════…
第2章
...
```

### Resumability & integrity
- `state.json` is **atomically** written (temp + `os.replace`, keeps `.bak`;
  `load_state` falls back to `.bak` if the main file is corrupt). Holds
  `oldest_url`, `newest_url`, and `scraped[]` (all complete novels — the loop guard).
- **Partial novels:** a chapter page that fails after retries leaves a
  `[页面获取失败: url]` placeholder; the preamble `完整性` line records it; the
  novel is logged to `logs/incomplete.log` and **kept out of `scraped[]`** so it's
  re-fetched. `scrape_from_walk.py --repair` re-downloads all such files.
- Idempotent: a COMPLETE existing file is skipped; an incomplete one is re-fetched.

---

## Catalogue facts (measured / estimated)

- **Span:** ~**Oct 2018 → present** (≈ 5.5+ years). A recommendation hop reached
  2018 novels; the chain alone had only reached ~2020 before.
- **The ~Nov-2020 origin region is a tangled bulk-import cluster whose prev-links
  loop** — handled by the loop guard / `visited` set; don't expect a clean single start.
- **Upload rate grows over time:** ~2/day (2020–21) → ~4–5/day (2023–26).
- **Total novels:** order **~7,000** (the 2023→2026 stretch is the bulk).
- **Size:** recent novels avg ~1.0 MB (≈115 pages); older novels run larger
  (200–600 pages). Blended **~8–10 GB** total. (`check_incomplete.py` reported 0
  incomplete across ~2,147 downloaded so far.)

## Suggested workflow for a full build

```bash
python3 scripts/inspect_urls.py                           # refresh data/url_map.json from output/
python3 scripts/create_catalogue.py --seed-map data/url_map.json
# then download exactly the live catalogue URLs, newest→oldest by default:
python3 scrape_from_catalogue.py --catalogue data/gl_catalog.json --workers 3
# or use direct newest→oldest plus Windscribe oldest→newest:
python3 scrape_from_catalogue.py --windscribe --workers 3
# use --forward for oldest→newest
python3 scripts/check_incomplete.py
# repair incomplete downloads if needed:
python3 scrape_from_walk.py --repair
```
The catalogue-free chain walk remains available through
`scrape_from_walk.py --backward` and `scrape_from_walk.py --forward`.
