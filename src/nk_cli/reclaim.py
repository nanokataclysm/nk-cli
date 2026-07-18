"""Dry-run disk reclaim reporter.

Never deletes files. Reports common cache/build debris under a root path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path


# Relative path fragments that are usually safe reclaim *candidates* for reporting.
CANDIDATE_NAMES = frozenset(
    {
        "node_modules",
        ".next",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".turbo",
        "dist",
        "build",
        ".cache",
    }
)
# Never report under these absolute-path prefixes (credentials / recovery).
BLOCKED_PREFIX_PARTS = (
    ".nanokat-secrets",
    ".ssh",
    ".gnupg",
    ".aws",
    ".config/gcloud",
)


@dataclass(frozen=True)
class Candidate:
    path: str
    kind: str
    size_bytes: int
    note: str


def _is_blocked(path: Path) -> bool:
    parts = path.parts
    joined = "/".join(parts)
    for block in BLOCKED_PREFIX_PARTS:
        if block in joined:
            return True
    return False


def _dir_size(path: Path, *, max_files: int = 50_000) -> int:
    total = 0
    count = 0
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            # prune blocked
            dirs[:] = [d for d in dirs if d not in {".git"}]
            for name in files:
                count += 1
                if count > max_files:
                    return total
                fp = Path(root) / name
                try:
                    total += fp.stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def scan(root: Path, *, max_depth: int = 6) -> list[Candidate]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {root}")
    if _is_blocked(root):
        raise PermissionError("root path is blocked from reclaim scans")

    found: list[Candidate] = []
    root_depth = len(root.parts)

    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        depth = len(current.parts) - root_depth
        if depth > max_depth:
            dirnames.clear()
            continue
        if _is_blocked(current):
            dirnames.clear()
            continue

        # Avoid descending into huge or private trees once matched
        keep: list[str] = []
        for name in list(dirnames):
            child = current / name
            if name in CANDIDATE_NAMES:
                size = _dir_size(child)
                found.append(
                    Candidate(
                        path=str(child),
                        kind=name,
                        size_bytes=size,
                        note="dry-run only — nk-cli never deletes",
                    )
                )
                # do not walk inside matched debris
                continue
            if name in {".git", ".venv", "venv"}:
                continue
            keep.append(name)
        dirnames[:] = keep

    found.sort(key=lambda c: c.size_bytes, reverse=True)
    return found


def format_human(candidates: list[Candidate]) -> str:
    if not candidates:
        return "reclaim: no common cache/build candidates found (dry-run)"
    lines = ["reclaim: dry-run report (no deletions)", ""]
    total = 0
    for c in candidates:
        total += c.size_bytes
        mib = c.size_bytes / (1024 * 1024)
        lines.append(f"  {mib:8.1f} MiB  {c.kind:16}  {c.path}")
    lines.append("")
    lines.append(f"total candidates: {len(candidates)}  ~{total / (1024 * 1024):.1f} MiB")
    lines.append("apply/delete is intentionally not implemented in public nk-cli")
    return "\n".join(lines)


def as_jsonable(candidates: list[Candidate]) -> list[dict]:
    return [asdict(c) for c in candidates]
