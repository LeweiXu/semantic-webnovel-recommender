# Task: Add scene breaks to `book.txt`

> **RESUME STATUS (as of last session):** 33 of ~63 windows committed, 153
> breaks recorded, cursor at line 4951. State is saved in
> `.scene_breaks_state.json`, so just pick up where it left off:
> run `python3 scene_breaks.py status`, then `python3 scene_breaks.py next`,
> and continue the loop below. Do NOT `reset`. When `next` says DONE, run
> `python3 scene_breaks.py build`.
>
> Notes from the work so far (keep doing this):
> - This novel cross-cuts heavily (often via a refrain like "每一颗棋子都以为
>   自己不在局中。比如X。", or "与此同时", or a bare location line like "端王府。"
>   / "寝殿内。" / "宫内。"). Treat **each cut to a different location/POV as its
>   own scene** — break at every switch, even in rapid A/B/A/B intercutting.
> - Long *continuous* set-pieces (a single fight, banquet, meeting, or night
>   that never changes place/time) stay **one** scene even at 100+ lines.
> - A character merely *entering or leaving* mid-scene is NOT a break; a
>   genuine location/time/POV change is.
> - Embedded backstory/flashback montages (e.g. 张三's history, 图尔/珊依) are
>   one scene each; don't split the montage internally.
> - The story is ~9421 lines; the back third is the climax (邶山 / 燕国 plot).


`book.txt` is a continuous Chinese web novel (《成何体统》) with **no chapter or
scene markers at all**. Your job is to infer where each scene ends and the next
begins, and mark those boundaries with `---`.

You do **not** edit the file by hand and you do **not** count lines yourself.
A helper script, `scene_breaks.py`, feeds you the text in overlapping windows and
performs the actual insertion. You only make judgments and report them.

A "break" is recorded as **"a new scene STARTS at line N"** — the script inserts
a single line `    ---` (4 spaces + ---) immediately *before* line N.

---

## How to run it (the loop)

Work from this directory. Repeat these steps until done:

1. `python3 scene_breaks.py status` — see where you are (safe to resume anytime).
2. `python3 scene_breaks.py next` — prints the next window. It shows:
   - a **commit zone** (lines marked with `>`): the only lines you may break before;
   - **context** lines before and after (no `>`): for judgment only, never break there.
3. Read the whole window. Decide which commit-zone lines begin a new scene.
4. Record it:
   - with breaks: `python3 scene_breaks.py commit <k> <lineA> <lineB> ...`
   - none this window: `python3 scene_breaks.py commit <k>`
   (`<k>` is the window number printed by `next`; the script rejects a wrong number.)
5. Go back to step 2. When `next` prints `DONE`, run:
   `python3 scene_breaks.py build`  → writes `book.scenes.txt` (original untouched).

If you realize you misjudged the window you just committed, run
`python3 scene_breaks.py back` to undo it and redo it.

There are ~63 windows. Keep going steadily; the state file makes it resumable.

---

## What counts as a scene break — MODERATE sensitivity

Mark a break at the line where a **new scene** begins. Use these triggers:

**Hard breaks (always):**
- **Time skip** — the narrative jumps forward/back ("次日", "几日后", "三天后",
  "片刻后" when it skips, "入夜", "翌日清晨", a flashback starting/ending).
- **Location change** — the action moves to a different place / setting.
- **POV or focus change** — the camera follows a different character or a
  different group than the previous scene.
- **Cut to a parallel thread** — meanwhile, elsewhere, another storyline.

**Soft breaks (include these at MODERATE level):**
- A scene's business clearly concludes and a **distinctly new activity or beat**
  starts, even in the same place — e.g. a conversation ends and something
  unrelated begins. Use judgment; don't break on mere topic drift inside one
  continuous conversation or action.

**Do NOT break:**
- inside continuous dialogue or action of the same scene;
- on minor topic shifts within one ongoing conversation;
- before line 1, or anywhere in the front matter (the disclaimer line, the title
  line `《成何体统》...`, and the `文案` blurb, roughly lines 1–8). The story proper
  begins around line 9; you may optionally place one break before line 9 to
  separate the blurb from the story, but never break inside the blurb.

### IMPORTANT — trace breaks back to their true start
A scene change is often only obvious **several lines after it actually began**
(that is the whole reason these breaks are missing). Read the trailing context,
and when you recognize a new scene, place the break before the **first** line
that belongs to the new scene — which may be earlier in the commit zone than
where you first noticed it. Short transitional lines like `几日后`, `寝殿内`,
`怡红院` are usually the **first line of the new scene**, so the break goes
*before* that line.

Lean toward catching real transitions (moderate), but every break must be a
defensible scene change, not a paragraph break.

### Calibrated taste (locked after reviewing windows 1-3, ~1 break / 35 lines)
- DO break on: time skips (第二天/翌日/入夜后/翌日清晨/是夜...), location changes
  (帝王寝殿/御书房/御花园...), POV switches (e.g. to 谢永儿, to 胥尧).
- Do NOT break on same-place immediate aftermaths or small in-place skips:
  e.g. a private debrief right after a banquet ("宫宴结束…"), "十分钟后" in the
  same room, "going to bed" after the same evening's dinner. Fold these into the
  prior scene.
- DO keep one structural break before the story's first line (~L9), separating
  the 文案 blurb from the story.

---

## Notes
- All line numbers are 1-indexed and match the source file and the `next` output.
- You can only break before lines inside the current window's commit zone; the
  overlap guarantees every boundary is judged with full context, so a break you
  "miss" at a window's edge will reappear in the commit zone of the next window.
- Don't modify `book.txt`. The result lands in `book.scenes.txt`.
