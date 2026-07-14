#!/usr/bin/env python3
"""Scene-break annotator for a continuous Chinese novel (e.g. book.txt).

The book has no chapter or scene markers. We must INFER scene breaks ("---")
from the narrative. To do that reliably we split the text into OVERLAPPING
windows, each with a "commit zone" plus leading/trailing context:

    [ lead context | COMMIT ZONE | trail context ]
       ~LEAD lines   ~COMMIT lines   ~TRAIL lines

Decisions (where a break goes) are only made for lines inside the commit zone,
but the model gets LEAD lines before and TRAIL lines after as context. Because
each window advances by exactly COMMIT lines, every line boundary lands in the
commit zone of exactly one window -- always with full context on both sides.
This is what prevents a break near a chunk edge from being missed.

The TRAIL context is generous on purpose: a new scene is frequently only
recognizable several lines after it actually starts, so the model needs to see
ahead and then trace the break back to the true first line of the new scene.

This script does the chunking + the (deterministic, verified) insertion.
A language-model session does the judgment, driving it like this:

    python scene_breaks.py status                 # progress (resumable)
    python scene_breaks.py next                    # show the next window
    python scene_breaks.py commit <k> [lines...]   # record breaks for window k
    python scene_breaks.py back                     # redo the previous window
    python scene_breaks.py build [out]              # write annotated file
    python scene_breaks.py reset                     # discard progress

A "break before line N" means: a new scene starts at line N. On build, the
inserter places a single line `    ---` (4 spaces + ---) immediately before line N.
All line numbers are 1-indexed and match the source file.
"""
import argparse
import json
import os
import sys

INPUT_DEFAULT = "book.txt"
STATE_FILE = ".scene_breaks_state.json"

# Window geometry. Tune here if needed (reset afterwards).
COMMIT = 150   # lines per commit zone -- decisions are made for these lines
LEAD = 30      # leading context lines shown before the commit zone (judge only)
TRAIL = 60     # trailing context lines shown after  the commit zone (judge only)


def load_lines(path):
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def snippet(line, n=16):
    """A stable fingerprint of a line: strip leading full-width spaces, take head."""
    return line.lstrip("　 \t").strip()[:n]


def init_state(input_path):
    lines = load_lines(input_path)
    return {
        "input": input_path,
        "commit": COMMIT,
        "lead": LEAD,
        "trail": TRAIL,
        "total": len(lines),
        "cursor": 1,          # 1-indexed first line of the NEXT window's commit zone
        "windows_done": 0,
        "breaks": [],         # [{"line": int, "snippet": str}], kept sorted by line
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def total_windows(st):
    return (st["total"] + st["commit"] - 1) // st["commit"]


def cmd_next(st, lines):
    cs = st["cursor"]
    total = st["total"]
    if cs > total:
        print("DONE -- all windows processed.")
        print(f"Breaks recorded: {len(st['breaks'])}")
        print("Next step:  python scene_breaks.py build")
        return
    C = st["commit"]
    ce = min(cs + C - 1, total)
    ds = max(1, cs - st["lead"])
    de = min(total, ce + st["trail"])
    k = st["windows_done"] + 1
    print(f"WINDOW {k} of ~{total_windows(st)}")
    print(f"Commit zone : lines {cs}..{ce}   <-- you may break BEFORE any of these lines")
    print(f"Context     : lines {ds}..{de}   (lines outside the commit zone are context only)")
    print(f"Breaks so far: {len(st['breaks'])}")
    print("-" * 70)
    for i in range(ds, de + 1):
        marker = ">" if cs <= i <= ce else " "
        print(f"{marker}{i:6d}| {lines[i - 1]}")
    print("-" * 70)
    print("Record your decision (a new scene STARTS at each listed line):")
    print(f"  python scene_breaks.py commit {k} <line> <line> ...")
    print(f"  python scene_breaks.py commit {k}        # if no breaks in this window")


def cmd_commit(st, lines, k, break_lines):
    cs = st["cursor"]
    total = st["total"]
    if cs > total:
        print("All windows already processed. Nothing to commit.")
        return
    expected = st["windows_done"] + 1
    if k != expected:
        print(f"ERROR: expected window {expected}, but got {k}.")
        print("Run `python scene_breaks.py next` to re-sync, then commit that window.")
        sys.exit(1)
    C = st["commit"]
    ce = min(cs + C - 1, total)
    for ln in break_lines:
        if ln <= 1:
            print(f"ERROR: cannot break before line {ln}.")
            sys.exit(1)
        if not (cs <= ln <= ce):
            print(f"ERROR: line {ln} is outside this window's commit zone {cs}..{ce}.")
            print("No changes made. Only break before lines marked '>' in `next`.")
            sys.exit(1)
    existing = {b["line"] for b in st["breaks"]}
    added = 0
    for ln in sorted(set(break_lines)):
        if ln in existing:
            continue
        st["breaks"].append({"line": ln, "snippet": snippet(lines[ln - 1])})
        added += 1
    st["breaks"].sort(key=lambda b: b["line"])
    st["windows_done"] = k
    st["cursor"] = cs + C
    save_state(st)
    print(f"Window {k} committed: +{added} break(s). Total: {len(st['breaks'])}. "
          f"Cursor -> line {st['cursor']}.")
    if st["cursor"] > total:
        print("That was the last window. Next step:  python scene_breaks.py build")


def cmd_back(st):
    if st["windows_done"] == 0:
        print("Already at the first window; nothing to undo.")
        return
    C = st["commit"]
    st["cursor"] = max(1, st["cursor"] - C)
    st["windows_done"] -= 1
    cs = st["cursor"]
    removed = [b for b in st["breaks"] if b["line"] >= cs]
    st["breaks"] = [b for b in st["breaks"] if b["line"] < cs]
    save_state(st)
    print(f"Stepped back to window {st['windows_done'] + 1} (commit zone starts at line {cs}).")
    print(f"Removed {len(removed)} break(s) at/after line {cs}. Run `next` to redo it.")


def cmd_status(st):
    print(f"Input   : {st['input']}  ({st['total']} lines)")
    print(f"Geometry: commit={st['commit']} lead={st['lead']} trail={st['trail']}")
    print(f"Progress: {st['windows_done']} / {total_windows(st)} windows   "
          f"cursor -> line {st['cursor']}")
    print(f"Breaks  : {len(st['breaks'])}")
    if st["cursor"] > st["total"]:
        print("Status  : COMPLETE -- ready to build.")
    else:
        print("Status  : in progress -- run `next`.")


def cmd_build(st, lines, out, force):
    if st["cursor"] <= st["total"] and not force:
        print(f"Not all windows processed (cursor at line {st['cursor']} / {st['total']}).")
        print("Finish the windows, or pass --force to build with what's recorded.")
        sys.exit(1)
    mismatches = []
    for b in st["breaks"]:
        ln = b["line"]
        if ln < 1 or ln > len(lines) or snippet(lines[ln - 1]) != b["snippet"]:
            mismatches.append(b)
    if mismatches:
        print("ERROR: snippet mismatch -- the input file changed since recording:")
        for b in mismatches[:10]:
            print(f"  line {b['line']}: expected starts with {b['snippet']!r}")
        sys.exit(1)
    breakset = {b["line"] for b in st["breaks"]}
    outlines = []
    for i, line in enumerate(lines, start=1):
        if i in breakset:
            outlines.append("    ---")
        outlines.append(line)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(outlines) + "\n")
    print(f"Wrote {out}: {len(lines)} source lines + {len(breakset)} scene breaks.")


def main():
    ap = argparse.ArgumentParser(description="Infer and insert scene breaks into a novel.")
    ap.add_argument("--input", default=INPUT_DEFAULT, help="source text (default: book.txt)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("next", help="show the next window")
    c = sub.add_parser("commit", help="record breaks for a window")
    c.add_argument("window", type=int)
    c.add_argument("lines", type=int, nargs="*")
    sub.add_parser("back", help="undo the previous window and redo it")
    sub.add_parser("status", help="show progress")
    b = sub.add_parser("build", help="write the annotated file")
    b.add_argument("out", nargs="?", default="book.scenes.txt")
    b.add_argument("--force", action="store_true")
    sub.add_parser("reset", help="discard all progress")

    args = ap.parse_args()

    if args.cmd == "reset":
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            print("State cleared.")
        else:
            print("No state to clear.")
        return

    st = load_state()
    if st is None:
        st = init_state(args.input)
        save_state(st)
    lines = load_lines(st["input"])
    if len(lines) != st["total"]:
        print(f"WARNING: {st['input']} now has {len(lines)} lines but state expects "
              f"{st['total']}. Reset if you changed the input.")

    if args.cmd == "next":
        cmd_next(st, lines)
    elif args.cmd == "commit":
        cmd_commit(st, lines, args.window, args.lines)
    elif args.cmd == "back":
        cmd_back(st)
    elif args.cmd == "status":
        cmd_status(st)
    elif args.cmd == "build":
        cmd_build(st, lines, args.out, args.force)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # output was piped into a reader that closed early (e.g. `| head`)
        try:
            sys.stdout.close()
        except Exception:
            pass
