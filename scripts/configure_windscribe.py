#!/usr/bin/env python3
"""Configure Windscribe CLI-only for direct plus VPN-proxy catalogue scraping."""
from __future__ import annotations

import argparse
import configparser
import shutil
import subprocess
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".config" / "Windscribe" / "windscribe_cli.conf"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()

    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    binary = shutil.which("windscribe-cli")
    if not binary:
        parser.error("windscribe-cli is not installed")

    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    if args.config.exists():
        config.read(args.config, encoding="utf-8")
    if not config.has_section("Connection"):
        config.add_section("Connection")
    connection = config["Connection"]

    connection["ShareProxyGatewayEnabled"] = "true"
    connection["ShareProxyGatewayMode"] = "HTTP"
    connection["ShareProxyGatewayPort"] = str(args.port)
    connection["ShareProxyGatewayWhileConnected"] = "true"
    connection["SplitTunnelingEnabled"] = "true"
    connection["SplitTunnelingMode"] = "Include"
    connection["SplitTunnelingApps"] = ""

    args.config.parent.mkdir(parents=True, exist_ok=True)
    with args.config.open("w", encoding="utf-8") as handle:
        config.write(handle)

    reloaded = subprocess.run(
        [binary, "preferences", "reload"],
        check=False,
        text=True,
    )
    if reloaded.returncode != 0:
        return reloaded.returncode

    print(f"Configured Windscribe HTTP proxy gateway at 127.0.0.1:{args.port}")
    print("Configured split tunneling in Include mode (only Windscribe uses the VPN).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
