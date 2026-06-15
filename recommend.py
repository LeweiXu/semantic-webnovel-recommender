#!/usr/bin/env python3
"""Entry point for the local GL-novel recommendation system.

Run with the recsys venv, e.g.:

    ~/venvs/recsys/bin/python recommend.py update
    ~/venvs/recsys/bin/python recommend.py like "Love U2"
    ~/venvs/recsys/bin/python recommend.py query "clingy detective x cold ex, ABO"

See `recommend.py -h` and `recommend.py <command> -h` for options.
"""
from __future__ import annotations

import sys

from recsys.cli import main

if __name__ == "__main__":
    sys.exit(main())
