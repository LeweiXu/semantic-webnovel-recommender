"""Network route helpers shared by the downloader and the metadata crawler.

Provides a direct route plus an optional Windscribe-tunnel route so site fetches
can be split across two distinct public IPs. Sessions are bound to specific
network interfaces (``if!<iface>``) so the direct and tunnel routes do not
collapse onto the same exit IP.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from curl_cffi import requests as cffi_requests

PUBLIC_IP_URL = "https://api.ipify.org"


@dataclass
class Route:
    label: str
    session: "cffi_requests.Session"


def windscribe_cli(*args: str) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("windscribe-cli")
    if not binary:
        raise RuntimeError("windscribe-cli is not installed")
    return subprocess.run(
        [binary, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _interfaces() -> tuple[str, str]:
    """Return (direct_interface, tunnel_interface) from the live routing table."""
    route = subprocess.run(
        ["ip", "route", "show", "default"],
        check=False, capture_output=True, text=True,
    )
    direct = "eth0"
    for line in route.stdout.splitlines():
        fields = line.split()
        if "dev" in fields:
            candidate = fields[fields.index("dev") + 1]
            if not candidate.startswith(("utun", "tun", "wg")):
                direct = candidate
                break
    links = subprocess.run(
        ["ip", "-o", "-4", "addr", "show"],
        check=False, capture_output=True, text=True,
    )
    tunnel = next(
        (
            line.split()[1]
            for line in links.stdout.splitlines()
            if len(line.split()) > 1 and line.split()[1].startswith(("utun", "tun", "wg"))
        ),
        "",
    )
    return direct, tunnel


def ensure_windscribe(location: str) -> tuple[str, str]:
    """Connect Windscribe if needed, disable its firewall, and return interfaces."""
    status = windscribe_cli("status")
    text = f"{status.stdout}\n{status.stderr}"
    if status.returncode or "Login state: Logged in" not in text:
        raise RuntimeError("Windscribe is not logged in")
    if "Connect state: Connected:" not in text:
        result = windscribe_cli("connect", location)
        if result.returncode:
            raise RuntimeError((result.stdout + result.stderr).strip())
    firewall = windscribe_cli("firewall", "off")
    if firewall.returncode:
        raise RuntimeError("Could not disable the Windscribe firewall")
    direct, tunnel = _interfaces()
    if not tunnel:
        raise RuntimeError("No Windscribe tunnel interface is active")
    return direct, tunnel


def public_ip(session: cffi_requests.Session) -> str:
    response = session.get(PUBLIC_IP_URL, timeout=15)
    response.raise_for_status()
    return response.text.strip()


def open_windscribe_routes(
    location: str = "best",
    *,
    direct_interface: str | None = None,
    skip_route_check: bool = False,
    emit=print,
) -> list[Route]:
    """Open interface-bound direct and Windscribe sessions.

    Returns ``[Route("direct", …), Route("windscribe", …)]``. Unless
    ``skip_route_check`` is set, the two public IPs are compared and a matching
    pair (routes collapsed onto one exit) raises ``RuntimeError``. The caller
    owns the returned sessions and must close them.
    """
    direct, tunnel = ensure_windscribe(location)
    direct = direct_interface or direct
    direct_session = cffi_requests.Session(interface=f"if!{direct}")
    vpn_session = cffi_requests.Session(interface=f"if!{tunnel}")
    try:
        if not skip_route_check:
            direct_ip, vpn_ip = public_ip(direct_session), public_ip(vpn_session)
            emit(f"Routes: direct={direct_ip}  windscribe={vpn_ip}")
            if direct_ip == vpn_ip:
                raise RuntimeError("Direct and Windscribe routes have the same public IP")
    except Exception:
        direct_session.close()
        vpn_session.close()
        raise
    return [Route("direct", direct_session), Route("windscribe", vpn_session)]
