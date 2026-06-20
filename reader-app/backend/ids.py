"""Novel identity helpers.

A novel's canonical key is its 52shuku landing URL. To put it in a clean path
segment we base64url-encode it (no padding), giving a reversible opaque id.
"""
from __future__ import annotations

import base64


def nid_encode(url: str) -> str:
    raw = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii")
    return raw.rstrip("=")


def nid_decode(nid: str) -> str:
    pad = "=" * (-len(nid) % 4)
    return base64.urlsafe_b64decode((nid + pad).encode("ascii")).decode("utf-8")
