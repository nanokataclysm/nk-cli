"""Discover possible push endpoints without authentication or data transfer."""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import socket
import threading
import time
from urllib.parse import urlsplit

from nk_cli.discovery import executable_on_path, run_metadata_command

MAX_TARGETS = 128
MAX_PROBES = 16
PROBE_TIMEOUT = 0.6
_PRIVATE = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))
_SHARED = ipaddress.ip_network("100.64.0.0/10")


def _host(value: str) -> str:
    if not value or len(value) > 253 or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ValueError("invalid target host")
    if "%" in value:
        raise ValueError("scoped or encoded target hosts are not supported")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        hostname = value.rstrip(".").lower()
        if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in hostname.split(".")):
            raise ValueError("invalid target host") from None
        return hostname
    if address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255":
        raise ValueError("target must be an individual host")
    return str(address)


def _port(value: str | int | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,5}", str(value)) or not 1 <= int(value) <= 65535:
        raise ValueError("target port must be between 1 and 65535")
    return int(value)


def parse_target(value: str) -> dict:
    """Keep only endpoint fields, never URL userinfo, repository paths or tokens."""
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise ValueError("target must be a nonempty host or supported URL")
    if value.startswith("-") or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("invalid target characters")
    if "://" in value:
        try:
            url = urlsplit(value)
            if url.scheme not in {"ssh", "https", "http", "git"}:
                raise ValueError("unsupported target transport")
            if not url.netloc or url.hostname is None:
                raise ValueError("missing target host")
            default = {"ssh": 22, "https": 443, "http": 80, "git": 9418}[url.scheme]
            return {"host": _host(url.hostname), "port": _port(url.port, default), "transport": url.scheme}
        except (ValueError, UnicodeError):
            raise ValueError("invalid or unsupported target URL") from None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        return {"host": _host(str(address)), "port": 22, "transport": "ssh"}
    if "::" in value and "[" not in value or re.match(r"^[A-Za-z]:[\\/]", value):
        raise ValueError("unsupported target notation")
    # SCP-style Git remotes, including user@[IPv6]:path. User and path are discarded.
    scp = re.fullmatch(r"(?:[^@\s/:]+@)?(\[[^\]]+\]|[^\s/:@]+):(.+)", value)
    if scp and ("@" in value or not scp.group(2).isdigit()):
        host = scp.group(1).removeprefix("[").removesuffix("]")
        return {"host": _host(host), "port": 22, "transport": "ssh"}
    bracket = re.fullmatch(r"\[([^\]]+)\](?::([0-9]+))?", value)
    if bracket:
        return {"host": _host(bracket.group(1)), "port": _port(bracket.group(2), 22), "transport": "ssh"}
    if value.count(":") > 1:
        return {"host": _host(value), "port": 22, "transport": "ssh"}
    host, separator, port = value.partition(":")
    return {"host": _host(host), "port": _port(port if separator else None, 22), "transport": "ssh"}


def _parse_git_target(value: str) -> dict:
    # Plain names and filesystem paths are local Git remotes, not SSH endpoints.
    if "://" in value:
        return parse_target(value)
    if (not value or value.startswith("-") or re.match(r"^[A-Za-z]:[\\/]", value)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("unsupported Git remote")
    scp = re.fullmatch(r"(?:[^@\s/:]+@)?(\[[^\]]+\]|[^\s/:@]+):([^:].*)", value)
    if not scp:
        raise ValueError("local or unsupported Git remote")
    host = scp.group(1).removeprefix("[").removesuffix("]")
    return {"host": _host(host), "port": 22, "transport": "ssh"}


def classify_network(host: str, tailscale_addresses: set[str] | None = None) -> str:
    if host == "localhost":
        return "loopback"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "unresolved"
    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        return "link-local"
    if tailscale_addresses and str(address) in tailscale_addresses:
        return "tailscale"
    if any(address.version == network.version and address in network for network in _PRIVATE):
        return "lan"
    if address.version == 4 and address in _SHARED:
        return "shared-address-space"
    if address.is_global:
        return "wan"
    return "special-use"


def _command(name: str, args: list[str], repo: Path | None, *, env: dict[str, str] | None = None) -> bytes:
    executable = executable_on_path(name, repo=repo)
    if executable is None:
        raise ValueError(f"{name} is unavailable on the trusted PATH")
    return run_metadata_command([executable, *args], cwd=repo, env=env)


def _git_targets(repo: Path, warnings: list[str]) -> list[dict]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    args = ["-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", "-C", str(repo),
            "config", "--local", "--no-includes", "--null", "--get-regexp",
            r"^remote\..*\.(url|pushurl)$|^core\.repositoryformatversion$"]
    try:
        output = _command("git", args, repo, env=env).decode("utf-8")
    except (ValueError, UnicodeError):
        warnings.append("Git remote metadata could not be read; explicit and discovered targets are still available.")
        return []
    remotes: dict[str, dict[str, list[str]]] = {}
    for record in output.split("\0"):
        key, separator, value = record.partition("\n")
        if not separator:
            continue
        match = re.fullmatch(r"remote\.(.+)\.(url|pushurl)", key)
        if match:
            name, field = match.groups()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", name):
                warnings.append("A remote with an unsupported name was skipped.")
                continue
            remotes.setdefault(name, {}).setdefault(field, []).append(value)
    result = []
    for name, fields in remotes.items():
        selected = "pushurl" if fields.get("pushurl") else "url"
        for value in fields.get(selected, []):
            try:
                endpoint = _parse_git_target(value)
            except ValueError:
                warnings.append("A Git remote with a local path or unsupported URL was skipped.")
                continue
            result.append({**endpoint, "source": "git-remote", "remote": name,
                           "route": selected, "observation": "configured-remote"})
    return result


def _lan_targets(repo: Path | None, warnings: list[str]) -> list[dict]:
    system = platform.system()
    try:
        if system == "Linux":
            rows = json.loads(_command("ip", ["-j", "neigh", "show"], repo))
            if not isinstance(rows, list):
                raise ValueError("invalid neighbor data")
            values = [row.get("dst") for row in rows if isinstance(row, dict)]
        elif system in {"Darwin", "Windows"}:
            output = _command("arp", ["-an"] if system == "Darwin" else ["-a"], repo)
            # Native ARP headers can use a localized/OEM encoding; IPv4 fields
            # remain ASCII, so do not decode the surrounding text at all.
            pattern = rb"\((\d{1,3}(?:\.\d{1,3}){3})\)" if system == "Darwin" else rb"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s"
            values = [value.decode("ascii") for value in re.findall(pattern, output, re.MULTILINE)]
            warnings.append("LAN discovery on this platform reads cached IPv4 ARP neighbors only.")
        else:
            warnings.append("Passive LAN discovery is unavailable on this platform.")
            return []
    except (ValueError, UnicodeError):
        warnings.append("LAN neighbor metadata is unavailable or malformed; no network sweep was attempted.")
        return []
    result = []
    seen = set()
    for value in values:
        try:
            host = _host(value) if isinstance(value, str) else ""
            ipaddress.ip_address(host)
        except ValueError:
            continue
        if host in seen:
            continue
        seen.add(host)
        result.append({"host": host, "port": 22, "transport": "ssh", "source": "lan-neighbor",
                       "observation": "cached-neighbor", "port_basis": "conventional-ssh"})
    return result


def _tailscale_targets(repo: Path | None, warnings: list[str]) -> tuple[list[dict], set[str]]:
    try:
        status = json.loads(_command("tailscale", ["status", "--json"], repo))
        if not isinstance(status, dict) or not isinstance(status.get("Peer", {}), dict):
            raise ValueError("invalid Tailscale state")
    except (ValueError, UnicodeError):
        warnings.append("Tailscale status is unavailable or malformed; no login or state change was attempted.")
        return [], set()
    if status.get("BackendState") not in (None, "Running"):
        warnings.append("Tailscale is not running; any cached peers are observations only.")
    result = []
    addresses = set()
    malformed = False
    for peer in [status.get("Self", {}), *status.get("Peer", {}).values()]:
        if not isinstance(peer, dict) or not isinstance(peer.get("TailscaleIPs", []), list):
            malformed = True
            continue
        for value in peer.get("TailscaleIPs", []):
            try:
                host = _host(value) if isinstance(value, str) else ""
                ipaddress.ip_address(host)
            except ValueError:
                malformed = True
                continue
            if host in addresses:
                continue
            addresses.add(host)
            if peer is status.get("Self"):
                continue
            result.append({"host": host, "port": 22, "transport": "ssh", "source": "tailscale-peer",
                           "observation": "tailnet-peer", "port_basis": "conventional-ssh",
                           "peer_online": peer.get("Online") if isinstance(peer.get("Online"), bool) else None})
    if malformed:
        warnings.append("Some malformed Tailscale peer records were skipped.")
    return result, addresses


def _resolve_host(host: str, port: int, timeout: float) -> list[tuple]:
    """Bound resolver waiting too: socket connection timeouts alone do not bound DNS."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        sockaddr = (host, port, 0, 0) if address.version == 6 else (host, port)
        return [(family, sockaddr)]
    done = threading.Event()
    result = []

    def resolve() -> None:
        try:
            for family, _, _, _, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
                try:
                    _host(sockaddr[0])
                except ValueError:
                    continue
                if family in {socket.AF_INET, socket.AF_INET6} and (family, sockaddr) not in result:
                    result.append((family, sockaddr))
        except OSError:
            pass
        finally:
            done.set()

    threading.Thread(target=resolve, daemon=True).start()
    if not done.wait(timeout):
        return []
    return result[:2]


def _probe(endpoint: dict) -> dict:
    deadline = time.monotonic() + PROBE_TIMEOUT
    resolved = _resolve_host(endpoint["host"], endpoint["port"], PROBE_TIMEOUT / 2)
    if not resolved:
        return {"reachability": "unresolved", "resolved_addresses": []}
    addresses = sorted({row[1][0] for row in resolved})
    for family, sockaddr in resolved:
        remaining = min(PROBE_TIMEOUT, deadline - time.monotonic())
        if remaining <= 0:
            break
        try:
            with socket.socket(family, socket.SOCK_STREAM) as connection:
                connection.settimeout(remaining)
                connection.connect(sockaddr)
            return {"reachability": "reachable", "resolved_addresses": addresses}
        except OSError:
            continue
    return {"reachability": "unreachable", "resolved_addresses": addresses}


def discover_targets(repo: Path | None, *, targets: tuple[str, ...] = (), lan: bool = False,
                     tailscale: bool = False, probe: bool = False) -> dict:
    """Report configured or observed endpoints; TCP success never proves push access."""
    if len(targets) > MAX_TARGETS:
        raise ValueError(f"at most {MAX_TARGETS} explicit targets are supported")
    warnings: list[str] = []
    candidates = []
    # Validate all explicit input before running metadata commands or opening connections.
    for value in targets:
        candidates.append({**parse_target(value), "source": "explicit", "observation": "user-specified"})
    if repo is not None:
        candidates.extend(_git_targets(repo, warnings))
    if lan:
        candidates.extend(_lan_targets(repo, warnings))
    known_tailscale: set[str] = set()
    if tailscale:
        peers, known_tailscale = _tailscale_targets(repo, warnings)
        candidates.extend(peers)
    if len(candidates) > MAX_TARGETS:
        warnings.append(f"Discovery was limited to {MAX_TARGETS} target candidates.")
        candidates = candidates[:MAX_TARGETS]
    probes: dict[tuple[str, int], dict] = {}
    for index, candidate in enumerate(candidates, 1):
        candidate.update(id=f"target-{index}", network=classify_network(candidate["host"], known_tailscale),
                         reachability="not-probed", push_ready=None)
        if not probe:
            continue
        key = (candidate["host"], candidate["port"])
        if key not in probes and len(probes) >= MAX_PROBES:
            candidate["reachability"] = "skipped-limit"
            continue
        if key not in probes:
            probes[key] = _probe(candidate)
        candidate.update(probes[key])
        if candidate["network"] == "unresolved" and candidate["resolved_addresses"]:
            networks = {classify_network(host, known_tailscale) for host in candidate["resolved_addresses"]}
            candidate["network"] = next(iter(networks)) if len(networks) == 1 else "mixed"
    if any(candidate["reachability"] == "skipped-limit" for candidate in candidates):
        warnings.append(f"TCP checks were limited to {MAX_PROBES} distinct endpoints.")
    return {"targets": candidates, "warnings": warnings}
