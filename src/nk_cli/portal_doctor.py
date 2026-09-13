"""Local TCP listener diagnostics; accepts simple services and legacy manifests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import socket
from typing import Any, Callable

from nk_cli.repository import CONFIG_VERSION, read_metadata

SUPPORTED_VERSIONS = frozenset(
    {
        "nk-portal-hosts/v1",
        "nanokat-portal-hosts/v1",  # monorepo schema compatibility only
        "nk-services/v1",
        CONFIG_VERSION,
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
        path = path.expanduser().absolute()
        payload = json.loads(read_metadata(path.parent, path.name))
    except (OSError, ValueError, RuntimeError) as exc:
        raise ManifestError("portal manifest cannot be read") from exc
    return normalize_manifest(payload)


def normalize_manifest(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("version"), str) or payload["version"] not in SUPPORTED_VERSIONS:
        raise ManifestError("portal manifest version is invalid")
    if payload["version"] in {"nk-services/v1", CONFIG_VERSION}:
        services = payload.get("services")
        if not isinstance(services, list):
            raise ManifestError("services must be a list")
        _scan_manifest(services)
        hosts = []
        for service in services:
            if not isinstance(service, dict):
                raise ManifestError("service entries must be objects")
            hosts.append({"id": service.get("id"), "required": service.get("required", True),
                          "listeners": [{"host": service.get("host", "127.0.0.1"),
                                         "port": service.get("port"), "bind_class": "localhost"}]})
    else:
        _scan_manifest(payload)
        hosts = payload.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        raise ManifestError("no local listeners configured; use --port or add services to .nk-cli.json")
    seen: set[str] = set()
    normalized = []
    for host in hosts:
        if not isinstance(host, dict):
            raise ManifestError("portal host entries must be objects")
        identifier = host.get("id")
        if not isinstance(identifier, str) or not SAFE_NAME.fullmatch(identifier) or identifier in seen:
            raise ManifestError("portal host id is invalid or duplicated")
        seen.add(identifier)
        if not isinstance(host.get("required", True), bool):
            raise ManifestError(f"portal host {identifier}: required must be a boolean")
        for key in ("role", "provider", "recovery"):
            if key in host and (not isinstance(host[key], str) or not host[key]):
                raise ManifestError(f"portal host {identifier} has invalid {key}")
        listeners = host.get("listeners", [])
        if not isinstance(listeners, list):
            raise ManifestError(f"portal host {identifier}: listeners must be a list")
        normalized_listeners = []
        for listener in listeners:
            if not isinstance(listener, dict):
                raise ManifestError(f"portal host {identifier} has invalid listener")
            if listener.get("host", "127.0.0.1") not in ("127.0.0.1", "localhost", "::1"):
                raise ManifestError(
                    f"portal host {identifier}: only loopback listeners are supported"
                )
            port = listener.get("port")
            if type(port) is not int or not 1 <= port <= 65535:
                raise ManifestError(f"portal host {identifier} has invalid listener port")
            if listener.get("bind_class", "localhost") != "localhost":
                raise ManifestError(f"portal host {identifier} has invalid listener class")
            address = listener.get("host", "127.0.0.1")
            normalized_listeners.append({"host": "127.0.0.1" if address == "localhost" else address,
                                         "port": port, "bind_class": "localhost"})
        normalized.append({**host, "listeners": normalized_listeners})
    return {"version": "nk-portal-hosts/v1", "hosts": normalized}


def listener_check(
    host: str,
    port: int,
    connector: Callable[..., Any] = socket.create_connection,
) -> CheckResult:
    if host == "localhost":
        host = "127.0.0.1"
    if host not in ("127.0.0.1", "::1") or type(port) is not int or not 1 <= port <= 65535:
        return CheckResult("listener", "misconfigured", "only valid loopback ports are checked")
    try:
        connection = connector((host, port), timeout=0.3)
        connection.close()
        return CheckResult("listener", "verified", f"{host}:{port} accepts TCP connections")
    except OSError:
        return CheckResult("listener", "offline", f"{host}:{port} is not accepting TCP connections")


def overall_status(checks: list[CheckResult]) -> str:
    if not checks:
        return "unknown"
    return max((check.status for check in checks), key=lambda status: STATUS_ORDER[status])


def inspect(
    manifest: dict[str, Any],
    *,
    connector: Callable[..., Any] = socket.create_connection,
) -> list[HostResult]:
    """Validate even direct Python callers before opening any sockets."""
    manifest = normalize_manifest(manifest)
    results: list[HostResult] = []
    for host in manifest["hosts"]:
        checks: list[CheckResult] = []
        for listener in host.get("listeners", []):
            checks.append(listener_check(listener["host"], listener["port"], connector))
        if not checks:
            checks.append(
                CheckResult("listeners", "unknown", "no localhost listeners declared")
            )
        status = overall_status(checks)
        results.append(
            HostResult(
                id=str(host["id"]),
                role=host.get("role", "service"),
                required=bool(host.get("required", True)),
                status=status,
                recovery=host.get("recovery", "unspecified"),
                checks=tuple(checks),
            )
        )
    return results
