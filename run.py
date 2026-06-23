#!/usr/bin/env python3
"""Start the web app: the reading room plus the semantic-recommender demo.

Quick start (from a fresh clone):

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt && pip install -e .
    python run.py

On the first run this installs the frontend's npm packages and builds the UI,
then serves the API + UI on one local port and opens your browser.

Options: --port N, --host H, --no-open, --rebuild.
Requires Node.js (https://nodejs.org) for the one-time frontend build.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "reader-app"))


def main() -> int:
    import serve  # reader-app/serve.py

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", action="store_true", help="Don't open a browser tab")
    ap.add_argument("--rebuild", action="store_true", help="Force a frontend rebuild")
    args = ap.parse_args()

    return serve.serve(
        args.host, args.port,
        open_browser_tab=not args.no_open, rebuild=args.rebuild,
    )


if __name__ == "__main__":
    raise SystemExit(main())
