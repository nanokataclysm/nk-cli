"""Validate a monorepo unit manifest and reject tracked runtime state.

Compatible with NANOKAT-style manifests (`nanokat-repository-units/v1`) and the
public schema (`nk-repository-units/v1`).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath

SUPPORTED_VERSIONS = frozenset(
    {
        "nk-repository-units/v1",
        "nanokat-repository-units/v1",  # monorepo compatibility
    }
)
FORBIDDEN_PARTS = {"node_modules", ".next", ".vercel", "__pycache__"}
FORBIDDEN_SUFFIXES = (".tsbuildinfo", ".chroma")


def tracked_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [item.decode() for item in result.stdout.split(b"\0") if item]


def forbidden_tracked(paths: list[str]) -> list[str]:
    bad: list[str] = []
    for value in paths:
        path = PurePosixPath(value)
        if FORBIDDEN_PARTS.intersection(path.parts):
            bad.append(value)
        elif any(part.startswith(".venv") for part in path.parts):
            bad.append(value)
        elif any(part.endswith(FORBIDDEN_SUFFIXES) for part in path.parts):
            bad.append(value)
        elif path.parts and path.parts[0].endswith(".chroma"):
            bad.append(value)
    return sorted(set(bad))


def validate_manifest(repo: Path, manifest: dict, tracked: list[str]) -> list[str]:
    errors: list[str] = []
    if manifest.get("version") not in SUPPORTED_VERSIONS:
        errors.append("unsupported manifest version")
    units = manifest.get("units")
    if not isinstance(units, list):
        return errors + ["units must be a list"]

    unit_paths = [unit.get("path") for unit in units if isinstance(unit, dict)]
    if len(unit_paths) != len(set(unit_paths)):
        errors.append("unit paths must be unique")
    for unit in units:
        if not isinstance(unit, dict):
            errors.append("every unit must be an object")
            continue
        path = unit.get("path")
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or ".." in PurePosixPath(path).parts
        ):
            errors.append(f"invalid unit path: {path!r}")
            continue
        if not (repo / path).exists():
            errors.append(f"manifest path does not exist: {path}")
        if (
            unit.get("kind") is None
            or unit.get("lifecycle") is None
            or not isinstance(unit.get("deploy_root"), bool)
        ):
            errors.append(f"unit metadata incomplete: {path}")

    allowed_roots = {
        PurePosixPath(path).parts[0]
        for path in unit_paths
        if isinstance(path, str) and path
    }
    allowed_files = set(manifest.get("root_files", []))
    for value in tracked:
        parts = PurePosixPath(value).parts
        if len(parts) == 1:
            if value not in allowed_files:
                errors.append(f"unclassified root file: {value}")
        elif parts[0] not in allowed_roots:
            errors.append(f"unclassified root path: {parts[0]}")

    package_units = {
        path.parent.name
        for path in repo.glob("*/package.json")
        if not path.parent.name.startswith(".")
    }
    missing_packages = sorted(package_units - set(unit_paths))
    errors.extend(f"package unit missing from manifest: {path}" for path in missing_packages)
    errors.extend(f"forbidden tracked runtime/generated path: {path}" for path in forbidden_tracked(tracked))
    return sorted(set(errors))


def run_boundaries(repo: Path, manifest_path: Path) -> tuple[int, list[str]]:
    repo = repo.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = validate_manifest(repo, manifest, tracked_files(repo))
    if errors:
        return 1, errors
    units = manifest.get("units") if isinstance(manifest.get("units"), list) else []
    return 0, [f"repository-boundaries: clean ({len(units)} units)"]
