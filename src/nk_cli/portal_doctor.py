"""Read-only portal / host health diagnostics.

Never logs in, repairs, starts, stops, restarts, or changes a remote system.
Mutable IP addresses and credential material are refused in the manifest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import socket
import subprocess
from typing import Any, Callable

SUPPORTED_VERSIONS = frozenset(
    {
        "nk-portal-hosts/v1",
        "nanokat-portal-hosts/v1",  # monorepo compatibility
    }
)
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
FORBIDDEN_FIELDS = frozenset(
    {"password", "secret", "token", "key", "private_key", "database_url", "ip", "ip_address"}
)
STATUS_ORDER = {"misconfigured": 5, "logged-out": 4, "offline": 3, "unknown": 2, "verified": 1}


@dataclass(frozen=True)
class CheckResult:
    check: str
    status: str
    detail: str


@dataclass(frozen=True)
class HostResult:
    id: str
    role: str
    required: bool
    status: str
    recovery: str
    checks: tuple[CheckResult, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["checks"] = [asdict(check) for check in self.checks]
        return value


class ManifestError(ValueError):
    pass


def _scan_manifest(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_FIELDS:
                raise ManifestError(f"forbidden manifest field at {path}.{key}")
            _scan_manifest(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_manifest(child, f"{path}[{index}]")


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError("portal manifest cannot be read") from exc
    if not isinstance(payload, dict) or payload.get("version") not in SUPPORTED_VERSIONS:
        raise ManifestError("portal manifest version is invalid")
    hosts = payload.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        raise ManifestError("portal manifest requires hosts")
    _scan_manifest(payload)
    seen: set[str] = set()
    for host in hosts:
        if not isinstance(host, dict):
            raise ManifestError("portal host entries must be objects")
        identifier = host.get("id")
        if not isinstance(identifier, str) or not SAFE_NAME.fullmatch(identifier) or identifier in seen:
            raise ManifestError("portal host id is invalid or duplicated")
        seen.add(identifier)
        for key in ("role", "provider", "recovery"):
            if not isinstance(host.get(key), str) or not host[key]:
                raise ManifestError(f"portal host {identifier} is missing {key}")
        for key in ("tailscale_name", "ssh_alias", "expected_user"):
            value = host.get(key)
            if value is not None and (not isinstance(value, str) or not SAFE_NAME.fullmatch(value)):
                raise ManifestError(f"portal host {identifier} has invalid {key}")
        for unit in host.get("units", []):
            if not isinstance(unit, str) or not SAFE_NAME.fullmatch(unit):
                raise ManifestError(f"portal host {identifier} has invalid unit")
        for listener in host.get("listeners", []):
            if not isinstance(listener, dict):
                raise ManifestError(f"portal host {identifier} has invalid listener")
            if listener.get("host") not in {"127.0.0.1", "tailnet"}:
                raise ManifestError(f"portal host {identifier} has invalid listener host")
            port = listener.get("port")
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise ManifestError(f"portal host {identifier} has invalid listener port")
            if listener.get("bind_class") not in {"localhost", "tailnet"}:
                raise ManifestError(f"portal host {identifier} has invalid listener class")
            expected_class = "localhost" if listener["host"] == "127.0.0.1" else "tailnet"
            if listener["bind_class"] != expected_class:
                raise ManifestError(f"portal host {identifier} listener host/class disagree")
    return payload


def run_command(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def tailscale_inventory(runner: Callable[..., subprocess.CompletedProcess[str]]) -> tuple[str, set[str]]:
    try:
        result = runner(["tailscale", "status", "--json"], timeout=5.0)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown", set()
    if result.returncode != 0:
        error = (result.stderr or result.stdout).lower()
        if "logged out" in error or "log in" in error:
            return "logged-out", set()
        if "not running" in error or "doesn't appear to be running" in error:
            return "offline", set()
        return "unknown", set()
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "misconfigured", set()
    names: set[str] = set()
    self_node = payload.get("Self", {})
    if isinstance(self_node, dict) and self_node.get("Online") is not False:
        for field in ("HostName", "DNSName"):
            value = self_node.get(field)
            if isinstance(value, str) and value:
                names.add(value.rstrip(".").split(".")[0])
    peers = payload.get("Peer", {})
    if isinstance(peers, dict):
        for peer in peers.values():
            if not isinstance(peer, dict) or peer.get("Online") is False:
                continue
            for field in ("HostName", "DNSName"):
                value = peer.get(field)
                if isinstance(value, str) and value:
                    names.add(value.rstrip(".").split(".")[0])
    return "verified", names


def ssh_config_check(
    alias: str,
    expected_user: str | None,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> CheckResult:
    try:
        result = runner(["ssh", "-G", alias], timeout=5.0)
    except (OSError, subprocess.TimeoutExpired):
        return CheckResult("ssh-config", "unknown", "ssh configuration could not be evaluated")
    if result.returncode != 0:
        return CheckResult("ssh-config", "misconfigured", "OpenSSH rejected the active configuration")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key in {"hostname", "user", "proxycommand"}:
            values[key] = value.strip()
    if expected_user and values.get("user") != expected_user:
        return CheckResult("ssh-config", "misconfigured", "configured SSH user does not match the manifest")
    return CheckResult("ssh-config", "verified", "alias and expected user resolve")


def listener_check(host: str, port: int, connector: Callable[..., Any] = socket.create_connection) -> CheckResult:
    if host != "127.0.0.1":
        return CheckResult("listener", "unknown", "non-local listener requires remote inspection")
    try:
        connection = connector((host, port), timeout=0.3)
        connection.close()
        return CheckResult("listener", "verified", f"localhost:{port} accepts connections")
    except OSError:
        return CheckResult("listener", "offline", f"localhost:{port} is not accepting connections")


def overall_status(checks: list[CheckResult]) -> str:
    if not checks:
        return "unknown"
    return max((check.status for check in checks), key=lambda status: STATUS_ORDER[status])


def inspect(
    manifest: dict[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
    connector: Callable[..., Any] = socket.create_connection,
) -> list[HostResult]:
    """Local-only inspection. Remote SSH unit checks stay monorepo-private."""
    tail_status, tail_names = tailscale_inventory(runner)
    results: list[HostResult] = []
    for host in manifest["hosts"]:
        checks: list[CheckResult] = []
        tail_name = host.get("tailscale_name")
        if tail_name:
            if tail_status != "verified":
                checks.append(CheckResult("tailscale", tail_status, "local tailnet inventory is unavailable"))
            elif tail_name in tail_names:
                checks.append(CheckResult("tailscale", "verified", "node is online by canonical name"))
            else:
                checks.append(CheckResult("tailscale", "offline", "node not present in local inventory"))
        alias = host.get("ssh_alias")
        if alias:
            checks.append(ssh_config_check(alias, host.get("expected_user"), runner))
        for listener in host.get("listeners", []):
            if listener.get("host") == "127.0.0.1":
                checks.append(listener_check("127.0.0.1", int(listener["port"]), connector))
            else:
                checks.append(
                    CheckResult(
                        "listener",
                        "unknown",
                        "tailnet listener checks require operator remote tooling (not in public nk-cli)",
                    )
                )
        status = overall_status(checks)
        results.append(
            HostResult(
                id=str(host["id"]),
                role=str(host["role"]),
                required=bool(host.get("required", True)),
                status=status,
                recovery=str(host["recovery"]),
                checks=tuple(checks),
            )
        )
    return results
