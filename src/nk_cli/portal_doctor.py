"""Read-only portal / host health diagnostics (public v1).

Public v1 is intentionally narrow:
- validate a local JSON manifest (no secrets / no raw IPs)
- check localhost listeners only

Blocked from this package (remain monorepo-private): Tailscale inventory,
remote SSH unit checks, LUKS/USB, secrets, ship, Alley/PTY, Kai, cloud backup.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import socket
from typing import Any, Callable

SUPPORTED_VERSIONS = frozenset(
    {
        "nk-portal-hosts/v1",
        "nanokat-portal-hosts/v1",  # monorepo schema compatibility only
    }
)
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
FORBIDDEN_FIELDS = frozenset(
    {
        "password",
        "secret",
        "token",
        "key",
        "private_key",
        "database_url",
        "ip",
        "ip_address",
        # mesh / remote control — not accepted in public manifests
        "tailscale_name",
        "ssh_alias",
        "expected_user",
        "units",
    }
)
STATUS_ORDER = {"misconfigured": 5, "offline": 3, "unknown": 2, "verified": 1}


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
        for listener in host.get("listeners", []):
            if not isinstance(listener, dict):
                raise ManifestError(f"portal host {identifier} has invalid listener")
            # Public v1: localhost only
            if listener.get("host") != "127.0.0.1":
                raise ManifestError(
                    f"portal host {identifier}: public v1 allows listener host 127.0.0.1 only"
                )
            port = listener.get("port")
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise ManifestError(f"portal host {identifier} has invalid listener port")
            if listener.get("bind_class") != "localhost":
                raise ManifestError(f"portal host {identifier} has invalid listener class")
    return payload


def listener_check(
    host: str,
    port: int,
    connector: Callable[..., Any] = socket.create_connection,
) -> CheckResult:
    if host != "127.0.0.1":
        return CheckResult("listener", "misconfigured", "public v1 only checks 127.0.0.1")
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
    connector: Callable[..., Any] = socket.create_connection,
) -> list[HostResult]:
    """Localhost-only inspection. No Tailscale, SSH, or remote unit probes."""
    results: list[HostResult] = []
    for host in manifest["hosts"]:
        checks: list[CheckResult] = []
        for listener in host.get("listeners", []):
            checks.append(listener_check("127.0.0.1", int(listener["port"]), connector))
        if not checks:
            checks.append(
                CheckResult("listeners", "unknown", "no localhost listeners declared")
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
