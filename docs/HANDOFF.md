# Handoff

Where the project stands as of 2026-09-20. Replaces the earlier handoffs from
2026-07-17 and 2026-08-27, both of which are in git history if you want them.
Deploy mechanics and server setup live in `docs/DEPLOYMENT.md`, not here.

## State

- Everything is committed and pushed to `origin/main`, and both halves are
  deployed. `novel-api` is active and `/api/health` returns
  `{"ok":true,"dictionary":true}`.
- 116 tests pass (`~/venvs/recsys/bin/python -m unittest discover -s tests`).
- Frontend builds clean (`cd reader-app/frontend && npm run build`).

## Library layout (the big one)

`library/` is grouped by source now, not by a flat list of categories:

```text
library/52shuku/<category>/metadata.jsonl, _catalog.jsonl, YYYY-MM/*.txt
library/uploads/metadata.jsonl, *.txt, and any folders dropped in by hand
```

Uploads aren't crawled and have no category split, so for them the source folder
is the store folder. `scripts/repo_paths.py` owns this shape (`category_dir`,
`metadata_path`, `catalog_path`, `store_dirs`, `source_for`) and nothing else
should join those paths by hand. `scraper.py` used to keep a second copy of the
layout and now routes through repo_paths too.

A record's `file` is always relative to `library/`, so moving the folders meant
repointing every record. `scripts/migrate_library_layout.py` does that, and it
only ever rewrites a path to one that exists on disk, so it can't invent a wrong
one and it's safe to re-run (a second pass finds nothing). It has already been
run on the server; the pre-migration metadata is backed up at
`~/library-metadata-backup-20260920/` there.

Worth knowing: `scripts/` is both a package and on `sys.path`, so
`scripts.repo_paths` and `repo_paths` can be two different module objects.
recsys imports the bare one, the reader backend the packaged one. Harmless in
production since both point at the same directory, but patching one in a test
silently misses the other. `tests/test_explorer.py` patches every loaded alias.

## Uploads are writable, crawled sources are not

The file explorer has a right-click menu (long-press on touch). Open, Add to
library and Download are offered everywhere; Rename and Delete only for files
under `uploads/`, and only for the admin account. Renaming or deleting a crawled
file would just desync it from its metadata.jsonl.

The backend decides that and ships a `managed` flag on each browse entry, so the
rule isn't re-derived from the path shape in the UI, and `browse.py` enforces it
server-side regardless of what the client offers. Deleting an indexed upload
drops its metadata record too; renaming one moves the record with the file and
keeps its `url`, which is what reading progress is keyed by. The shelf entry is
built from the file name, so after a rename it reappears next time you open the
book.

Search covers the uploads folder as well as the catalogue. Your own files lead
the results, capped at 8, because the 57k-record catalogue would otherwise fill
the page before an upload got a place.

## Reader features added recently

- **Word counts.** Per chapter in the reading view and in the novel page
  contents list (right-aligned), plus total and per-chapter average on the novel
  page. Counted in the unit the language uses: non-whitespace characters for
  Chinese, whitespace words for English.
- **Bookmarks.** Any number per novel, per account, in the Navigate drawer
  alongside the contents. The reading position is pinned at the top of that list
  as a special entry that can't be deleted. The drawer always opens on Contents,
  at the chapter you're reading.
- **Annotation accuracy.** jieba proposes the segmentation, CC-CEDICT settles
  it, so a word the dictionary knows is grouped the same way in every sentence
  (子时 used to split in some contexts and not others) and every multi-character
  group is a real dictionary word, which took the hover-lookup miss rate from
  27% to 0. A merge may not end on a grammatical particle, or 书中的人 reads
  zhòng dì instead of zhōng de.
- **Simplify Characters.** Admin button beside Join Split Sentences. Rewrites
  stray traditional characters in a novel's .txt. Only maps characters CC-CEDICT
  never writes in simplified text, which is what keeps it from wrecking 么, 宁,
  份, 座, 覆, 著 and 沈. One character in, one out, so offsets never move and
  bookmarks survive.

## Open items

- **Admin actions still not click-tested on the deployed site.** Join Split
  Sentences, Simplify Characters and the new rename/delete are all verified at
  the API level and by tests, but never exercised through the real UI, because
  that needs the `lingwei` password.
- **Em dashes.** CLAUDE.md says never use them anywhere, including code
  comments. About 40 files still have them, nearly all pre-existing
  (`scraper.py` alone has 19). The ones added in recent sessions are cleaned up.
  A repo-wide pass is still owed. Careful with test data: the `——` in
  `tests/test_explorer.py` is Chinese punctuation inside a fixture, not prose.
- **Library files with a chapter over the 20k rich-render cap** render without
  pinyin or the click dictionary. Their numbering gives no evidence they are
  fused, so the splitter deliberately leaves them alone.
- **Pinyin line spacing goes flat below about 1.75.** With the -0.3 offset the
  requested value lands under what ruby needs and the browser floors it, so the
  slider feels unresponsive down there.
- **Only the header nav uses real links.** Novel cards in Discover/Library are
  still buttons, so they can't be opened in a new tab.
- **Chapter indices shift** for any novel the fused-chapter splitter touches, so
  an old bookmark in one of those lands somewhere different once.
- **A stale progress entry** for `GL/妖刀姬.txt` (position 0) survives from the
  old browse root. It is ignored and harmless.

## Local environment

- `playwright` was pip-installed into `~/venvs/recsys` for browser checks but
  its browsers aren't downloaded; it can drive the system Chrome with
  `channel="chrome"` instead. Not in `requirements.txt`.
- The root directory is meant to hold only entry points and project files. Shell
  scripts live in `scripts/`, docs in `docs/`.
