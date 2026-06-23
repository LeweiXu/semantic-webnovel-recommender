#!/usr/bin/env python3
"""Run the reader app on a single local port and open it in the browser.

Builds the frontend if needed, then serves the API + built UI from one uvicorn
process. Run with the project venv so the editable imports resolve:

    ~/venvs/recsys/bin/python reader-app/serve.py [--port 8000] [--no-open] [--rebuild]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "frontend" / "dist"


def ensure_build(rebuild: bool) -> None:
    if DIST.exists() and not rebuild:
        return
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    if shutil.which(npm) is None:
        print("npm not found: install Node.js (https://nodejs.org), then re-run.",
              file=sys.stderr)
        raise SystemExit(1)
    frontend = HERE / "frontend"
    if not (frontend / "node_modules").exists():
        print("Installing frontend dependencies (npm install)…")
        subprocess.run([npm, "install"], cwd=frontend, check=True)
    print("Building the frontend…")
    subprocess.run([npm, "run", "build"], cwd=frontend, check=True)


def open_browser(url: str) -> None:
    # On WSL2, localhost is reachable from the Windows browser. Prefer wslview,
    # then explorer.exe, then the stdlib opener.
    for opener in (["wslview", url], ["explorer.exe", url]):
        if shutil.which(opener[0]):
            subprocess.Popen(opener, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    open_browser_tab: bool = True,
    rebuild: bool = False,
    open_path: str = "",
) -> int:
    """Build (if needed) and serve the reader app, optionally opening a browser.

    ``open_path`` is appended after the trailing slash, so callers can deep-link
    to a novel (e.g. ``"?open=<nid>&ch=<index>"``). Importable so other entry
    points (like ``read.py --gui``) can launch the reading room in-process.
    """
    ensure_build(rebuild)

    # Import uvicorn after the build so a missing dep points at setup.py clearly.
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed: run reader-app/setup.py first.", file=sys.stderr)
        return 1

    sys.path.insert(0, str(HERE / "backend"))
    url = f"http://{host}:{port}/{open_path}"
    if open_browser_tab:
        threading.Thread(
            target=lambda: (time.sleep(1.4), open_browser(url)), daemon=True
        ).start()
    print(f"Reading room → {url}")
    uvicorn.run("app:app", host=host, port=port, log_level="info")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-open", action="store_true", help="Don't open a browser")
    parser.add_argument("--rebuild", action="store_true", help="Force a frontend rebuild")
    args = parser.parse_args()

    return serve(
        args.host,
        args.port,
        open_browser_tab=not args.no_open,
        rebuild=args.rebuild,
    )


if __name__ == "__main__":
    raise SystemExit(main())
