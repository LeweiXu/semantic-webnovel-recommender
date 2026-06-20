#!/usr/bin/env python3
"""One-time setup for the reader app.

Installs the backend's extra Python deps into the CURRENT interpreter's
environment (run it with the project venv, e.g. ~/venvs/recsys/bin/python), and
installs the frontend's npm packages. The CC-CEDICT dictionary is vendored in
reader-app/data/, so there is nothing to download.

    ~/venvs/recsys/bin/python reader-app/setup.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    # Backend deps into the venv that's running this script (so the editable
    # webnovel/recsys/scraper imports keep working).
    run([sys.executable, "-m", "pip", "install", "-r",
         str(HERE / "backend" / "requirements.txt")])

    cedict = HERE / "data" / "cedict_ts.u8"
    if cedict.exists():
        print(f"\nCC-CEDICT present: {cedict} ({cedict.stat().st_size // 1024} KB)")
    else:
        print(f"\nWARNING: {cedict} is missing — definitions will be empty.")

    # Frontend deps.
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    run([npm, "install"], cwd=HERE / "frontend")

    print("\nSetup complete. Start the reader with:")
    print(f"    {sys.executable} reader-app/serve.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
