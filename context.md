# Project Context — 52shuku.net GL Novel Scraper

> Briefing for an agentic coding assistant (e.g. Claude Code). Read this fully
> before writing or changing code. It records the objective, the site's
> structure, what we've confirmed, and — critically — **approaches that do NOT
> work**, so they aren't re-attempted.

## Objective

Scrape **all GL (百合) novels** from `https://www.52shuku.net/` for personal
offline reading, and output each novel in multiple formats:

- `.epub` (primary)
- `.txt` (plain text)
- `.md` (Markdown, with embedded image links as `![](url)` where images exist)

The pipeline has two phases that are **interleaved**, not sequential (see
"Discovery strategy" below):

1. **Discovery** — find every GL novel URL.
2. **Scraping** — for each novel, fetch all its pages, assemble text/images,
   and serialise to the three output formats.

Language/stack: **Python** (preferred). Use `requests` + `lxml`/`BeautifulSoup`.
EPUB assembly via `ebooklib` (or hand-rolled zip if preferred).

## Site structure (confirmed)

- Novel landing page URL shape: `/{category}/{shard}/{id}.html`
  - Example GL: `https://www.52shuku.net/gl/06_b/bkec8.html`
  - `{category}` = genre folder (`gl`, `yanqing`, `xiandaidushi`, `bl`, ...)
  - `{shard}` = bucket like `04_b`, `06_b`, `07_b` (directory sharding)
  - `{id}` = short **base62** code (`0-9A-Za-z`), e.g. `bkec8`, `bkecR`
- Novel reading pages: `{id}_2.html`, `{id}_3.html`, ... The landing page
  lists all of them explicitly (开始阅读 / 第N页 links). No need to guess.
- Each novel landing page has navigation:
  - **上一篇** = previous novel (chronologically older upload)
  - **下一篇** = next novel (chronologically newer upload)
- **CONFIRMED by the user:** 上一篇 always leads to the previous GL novel in
  upload-time order. This makes the linked-list chain walk reliable for full
  coverage.
- Encoding: **UTF-8** (confirmed — sitemap and pages are clean UTF-8). Unlike
  the sister site jjwxc.net which is GB18030, we do NOT need special decoding.

### ID scheme history (important)
The base62 counter is **global across all categories** (a new novel in any
genre takes the next ID, then lands in its own category folder). The
`{shard}/{base62}.html` scheme is **recent**. Older novels use different URL
shapes seen in recommendation lists, e.g. `/gl/12764.html`, `/gl/hvsq.html`,
`/bl/27.html`. The chain walk handles all schemes transparently because each
page hands us the literal next URL.

## Discovery strategy — DECIDED

**Interleaved chain walk via 上一篇 (previous).**

Start from a recent known GL novel and follow **上一篇** backwards through the
entire GL catalogue (newest → oldest). Do NOT collect all URLs first and then
scrape — instead **scrape each novel as we reach it**, then follow its 上一篇
link to the next one. The chain *is* the work queue.

- **The URL is the resume/stop indicator.** Maintain a record (e.g. a
  checkpoint file or a per-novel output file named by id) of what's been
  scraped. To resume after interruption: start from the last-scraped novel's
  上一篇 link, or skip novels whose output already exists.
- **Stop condition:** when a novel has no 上一篇 link, or the 上一篇 link points
  outside `/gl/`. **Log and pause at this boundary** for human eyeballing
  rather than assuming — we have not empirically verified the exact oldest-novel
  endpoint, and the old-ID-scheme region is unverified territory.
- Forward direction (下一篇) is only needed to catch novels newer than the seed;
  seed from the current newest if you want everything.

## What does NOT work (do not re-attempt)

1. **Paginated index `/gl/index_N.html` (pages 1–80).** Only a *curated, recent*
   list. Page 80 bottoms out around 2025 uploads; novels older than that are
   NOT reachable via the index. ❌ Not complete.
2. **`sitemap.xml`.** It's a rolling *"recently updated"* feed for search
   engines (`changefreq=daily`), NOT an archive. Empirically: only **491 total
   URLs** site-wide, of which **50 are GL**, all very recent (June 2026 era).
   ❌ Not complete. (`sitemap_index.xml` returns 404.)
3. **`/so/` search endpoint.** Disallowed by robots.txt (`Disallow: /so/`), and
   query-string URLs are blocked too (`Disallow: /*?*`). ❌ Off-limits + blocked.
4. **Exhaustive base62 sweep of a shard's ID range.** Fails for two reasons:
   (a) IDs are *global across categories*, so most IDs in any range belong to
   other genres and 404 under `/gl/` → huge wasted/disrespectful request volume
   and a 404-heavy pattern that looks like abuse; (b) it only covers the modern
   `NN_b` scheme and **misses the entire old-ID back-catalogue** — the exact
   novels we're trying to reach. ❌ Incomplete + impolite. (May have niche use
   as gap-fill *within an already-confirmed* shard range, but not as primary.)

## robots.txt — how to behave

- `/gl/` is **allowed** under `User-agent: *`. We're within the rules.
- Honor these disallows: `/e/*`, `/*?*` (no query strings), `/d/*`, `/so/*`,
  `/templets`, `/404.html`, `/bookcase.html`, `/skin/52shuku/js/*`.
- The file blocks named AI/SEO bots (ClaudeBot, GPTBot, CCBot, Bytespider,
  AhrefsBot, Baiduspider, etc.) with `Disallow: /`. Use an honest generic
  browser-like User-Agent; do not impersonate those named bots, and do not
  impersonate Googlebot.
- `Content-Signal: ai-train=no` is declared (EU Copyright Directive Art. 4 TDM
  opt-out). This is **personal offline reading, not AI training** — but it
  signals a rights-conscious operator, so keep impact minimal and personal.

## Anti-bot reality (Cloudflare)

The site is behind **Cloudflare**. A blocked/missing request may NOT be a clean
404 — it can be HTTP 403/503 (or even 200) with a challenge body containing
`__CF$cv$params`, `challenge-platform`, or `Just a moment`, or a
timeout/reset. **Classify responses by sniffing the body, not just the status
code.** Distinguish:
- genuine 404 (real missing page),
- Cloudflare challenge (back off + retry, do NOT treat as "end of chain"),
- 429 rate-limit (back off hard).

Plain `curl`/`requests` got through for static files (robots.txt, sitemap.xml,
novel pages) in manual testing, so a browser engine may not be required — but
keep Playwright as a fallback if challenges escalate during a long run.

## Politeness / throttling — REQUIRED

This is a long job (hundreds–thousands of novels × multiple pages each). Be a
good citizen:
- **Randomized delay** between requests (jitter, not a fixed period).
- **Exponential backoff** on any CHALLENGED / 429 / error response.
- **Checkpoint after every novel** so the job is resumable and never re-fetches.
- Single-threaded / low concurrency. Do not parallelise aggressively.
- Run `probe_rate_limit.py` ONCE to characterise the safe request rate, then
  record the recommended delay HERE:

  > **Measured safe throttle:** 1.0s between requests (~60 req/min). All 50
  > requests clean through the full ramp (5s → 3s → 2s → 1s → 0.5s delay).
  > curl_cffi / chrome136 impersonation; no challenges triggered. Use 1.0s
  > base delay + jitter + exponential backoff on CHALLENGED/429.

## Current status / next steps

- [x] Decoded URL structure and ID scheme.
- [x] Ruled out index, sitemap, search, and base62-sweep as discovery methods.
- [x] Confirmed chain walk (上一篇) reaches full catalogue; confirmed UTF-8.
- [x] Wrote `probe_rate_limit.py` (rate-limit characterisation).
- [x] **Run `probe_rate_limit.py`; record safe delay above.**
- [ ] Build the interleaved chain-walk scraper:
      seed → scrape novel (all `_N.html` pages) → serialise epub/txt/md →
      checkpoint → follow 上一篇 → repeat; pause+log at chain boundary.
- [ ] Build the per-novel parser: extract title/author/status from landing
      page; extract chapter text + images from reading pages; strip site
      chrome (nav, recommendation lists, footer).
- [ ] Build the three serialisers (epub / txt / md-with-image-links) over a
      common in-memory novel representation.

## Files

- `probe_rate_limit.py` — safe, ramping rate-limit probe. Run once.
- `context.md` — this file.