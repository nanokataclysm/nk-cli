"""Dry-run disk reclaim reporter.

Never deletes files. Reports common cache/build debris under a root path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
import os
from pathlib import Path
import stat


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
        ".parcel-cache",
    }
)
# Never report under these absolute-path prefixes (credentials / recovery).
BLOCKED_PREFIX_PARTS = (
    ".git",
    ".nanokat-secrets",
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".config/gcloud",
)


@dataclass(frozen=True)
class Candidate:
    path: str
    kind: str
    size_bytes: int
    note: str
    size_complete: bool = True


def _is_blocked(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    for block in BLOCKED_PREFIX_PARTS:
        pattern = tuple(block.split("/"))
        if any(parts[i:i + len(pattern)] == pattern for i in range(len(parts))):
            return True
    return False


def _excluded(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(path.relative_to(root).as_posix(), pattern) for pattern in patterns)


def _dir_size(path: Path, root: Path, patterns: tuple[str, ...], *, max_files: int) -> tuple[int, bool]:
    total = 0
    count = 0
    complete = True
    seen: set[tuple[int, int]] = set()

    def inaccessible(_error: OSError) -> None:
        nonlocal complete
        complete = False

    for directory, dirs, files in os.walk(path, followlinks=False, onerror=inaccessible):
        current = Path(directory)
        keep = []
        for name in dirs:
            child = current / name
            if child.is_symlink() or name == ".git" or _is_blocked(child) or _excluded(child, root, patterns):
                complete = False
            else:
                keep.append(name)
        dirs[:] = keep
        for name in files:
            count += 1
            if count > max_files:
                return total, False
            child = current / name
            if _is_blocked(child) or _excluded(child, root, patterns):
                complete = False
                continue
            try:
                info = child.lstat()
                if not stat.S_ISREG(info.st_mode):
                    complete = False
                    continue
                identity = (info.st_dev, info.st_ino)
                if identity not in seen:
                    seen.add(identity)
                    total += info.st_size
            except OSError:
                complete = False
    return total, complete


def scan(
    root: Path, *, max_depth: int = 6, include_names: tuple[str, ...] = (),
    exclude_paths: tuple[str, ...] = (), max_files: int = 50_000,
    exclude_base: Path | None = None,
) -> list[Candidate]:
    if max_depth < 0 or max_files < 1:
        raise ValueError("depth must be nonnegative and max-files must be positive")
    for name in include_names:
        if not name or name in {".", "..", ".git"} or any(c in name for c in "/\\\0:") or _is_blocked(Path(name)):
            raise ValueError("cache names must be single, non-sensitive directory names")
    root = root.expanduser().resolve()
    exclude_base = exclude_base.resolve() if exclude_base is not None else root
    if not root.is_relative_to(exclude_base):
        raise ValueError("scan root must be inside its exclusion base")
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {root}")
    if _is_blocked(root):
        raise PermissionError("root path is blocked from reclaim scans")

    found: list[Candidate] = []
    names = CANDIDATE_NAMES | set(include_names)
    root_depth = len(root.parts)

    def add(path: Path) -> None:
        size, complete = _dir_size(path, exclude_base, exclude_paths, max_files=max_files)
        found.append(Candidate(str(path), path.name, size,
                               "logical file bytes; review before removing; nk-cli never deletes", complete))

    def fail(error: OSError) -> None:
        # Discovery failures must not look like a clean, complete inventory.
        raise error

    if _excluded(root, exclude_base, exclude_paths):
        return found
    if root.name in names:
        add(root)
        return found

    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False, onerror=fail):
        current = Path(dirpath)
        depth = len(current.parts) - root_depth
        if depth >= max_depth:
            dirnames.clear()
            continue
        if _is_blocked(current):
            dirnames.clear()
            continue

        # Avoid descending into huge or private trees once matched
        keep: list[str] = []
        for name in list(dirnames):
            child = current / name
            if child.is_symlink() or _is_blocked(child) or _excluded(child, exclude_base, exclude_paths):
                continue
            if name in names:
                add(child)
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
        suffix = " (partial size)" if not c.size_complete else ""
        lines.append(f"  {mib:8.1f} MiB  {c.kind:16}  {c.path}{suffix}")
    lines.append("")
    lines.append(f"total candidates: {len(candidates)}  ~{total / (1024 * 1024):.1f} MiB")
    lines.append("logical file sizes are estimates, not guaranteed freeable disk space")
    return "\n".join(lines)


def as_jsonable(candidates: list[Candidate]) -> list[dict]:
    return [asdict(c) for c in candidates]
