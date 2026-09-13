"""Inspect any Git working tree, with optional explicit directory boundaries."""

from __future__ import annotations

from fnmatch import fnmatchcase
import json
from pathlib import Path, PurePosixPath

from nk_cli.repository import CONFIG_NAME, CONFIG_VERSION, load_profile, read_metadata, repository_files, repository_root

SUPPORTED_VERSIONS = frozenset(
    {
        CONFIG_VERSION,
        "nk-repository-units/v1",
        "nanokat-repository-units/v1",  # monorepo compatibility
    }
)
FORBIDDEN_PARTS = {
    "node_modules", ".next", ".vercel", "__pycache__", ".venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".turbo",
}
FORBIDDEN_SUFFIXES = (".tsbuildinfo", ".chroma")


def tracked_files(repo: Path) -> list[str]:
    return repository_files(repo)


def forbidden_tracked(paths: list[str], allow_patterns: tuple[str, ...] = ()) -> list[str]:
    bad: list[str] = []
    for value in paths:
        if any(fnmatchcase(value, pattern) for pattern in allow_patterns):
            continue
        path = PurePosixPath(value)
        if FORBIDDEN_PARTS.intersection(path.parts):
            bad.append(value)
        elif any(part.endswith(FORBIDDEN_SUFFIXES) for part in path.parts):
            bad.append(value)
    return sorted(set(bad))


def _relative_path(value: object) -> bool:
    return (
        isinstance(value, str) and bool(value) and "\\" not in value
        and ":" not in value and "\0" not in value
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def validate_manifest(
    repo: Path, manifest: object, tracked: list[str], *, allow_patterns: tuple[str, ...] = (),
) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]

    if not isinstance(manifest.get("version"), str) or manifest["version"] not in SUPPORTED_VERSIONS:
        errors.append("unsupported manifest version")

    root_files = manifest.get("root_files")
    if not isinstance(root_files, list) or not all(
        _relative_path(path) and "/" not in path for path in root_files
    ):
        errors.append("root_files must be a list of filenames")
        root_files = []

    units = manifest.get("units")
    if not isinstance(units, list):
        return errors + ["units must be a list"]

    unit_paths: list[str] = []
    seen_paths: set[str] = set()
    for unit in units:
        path = unit.get("path") if isinstance(unit, dict) else unit
        if not _relative_path(path):
            errors.append(f"invalid unit path: {path!r}")
            continue
        if path in seen_paths:
            errors.append("unit paths must be unique")
        else:
            seen_paths.add(path)
            unit_paths.append(path)
        resolved = (repo / path).resolve()
        if not resolved.is_relative_to(repo.resolve()):
            errors.append(f"manifest path leaves repository: {path}")
        elif not resolved.is_dir():
            errors.append(f"manifest path does not exist: {path}")

    allowed_files = set(root_files)
    for value in tracked:
        if any(value == path or value.startswith(path + "/") for path in unit_paths):
            continue
        parts = PurePosixPath(value).parts
        if len(parts) == 1:
            if value not in allowed_files:
                errors.append(f"unclassified root file: {value}")
        else:
            errors.append(f"unclassified tracked path: {value}")

    allowed = manifest.get("allow_tracked", [])
    if not isinstance(allowed, list) or not all(isinstance(p, str) and p for p in allowed):
        errors.append("allow_tracked must be a list of nonempty glob patterns")
        allowed = []
    errors.extend(
        f"forbidden tracked runtime/generated path: {path}"
        for path in forbidden_tracked(tracked, (*allow_patterns, *allowed))
    )
    return sorted(set(errors))


def boundary_report(
    repo: Path, manifest_path: Path | None = None, *, allow_patterns: tuple[str, ...] = (),
) -> tuple[int, dict]:
    report = {"mode": "manifest" if manifest_path else "automatic", "errors": []}
    manifest = None
    if manifest_path is not None:
        try:
            manifest_path = manifest_path.expanduser().absolute()
            manifest = json.loads(read_metadata(manifest_path.parent, manifest_path.name))
        except json.JSONDecodeError:
            report["errors"] = ["manifest is not valid JSON"]
            return 2, report
        except (OSError, ValueError, RuntimeError):
            report["errors"] = ["manifest cannot be read"]
            return 2, report

    try:
        repo = repository_root(repo)
        tracked = tracked_files(repo)
        if manifest_path is None:
            manifest = load_profile(repo)
            if manifest is not None:
                report["mode"] = CONFIG_NAME
    except ValueError as exc:
        report["errors"] = [str(exc)]
        return 2, report

    report.update(
        repo=str(repo), tracked_files=len(tracked),
        root_files=[path for path in tracked if "/" not in path],
        units=sorted({path.split("/")[0] for path in tracked if "/" in path}),
    )
    if manifest is not None or manifest_path is not None:
        try:
            errors = validate_manifest(repo, manifest, tracked, allow_patterns=allow_patterns)
        except (OSError, RuntimeError):
            report["errors"] = ["manifest paths cannot be resolved"]
            return 2, report
    else:
        errors = [f"forbidden tracked runtime/generated path: {path}"
                  for path in forbidden_tracked(tracked, allow_patterns)]
    report["errors"] = errors
    return (1 if errors else 0), report


def run_boundaries(
    repo: Path, manifest_path: Path | None = None, *, allow_patterns: tuple[str, ...] = (),
) -> tuple[int, list[str]]:
    code, report = boundary_report(repo, manifest_path, allow_patterns=allow_patterns)
    return code, report["errors"] or [
        f"repository-boundaries: clean ({report['tracked_files']} tracked files; {report['mode']} mode)",
    ]
