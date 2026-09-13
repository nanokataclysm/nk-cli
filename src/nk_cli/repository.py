"""Read repository metadata without running project code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from nk_cli.discovery import executable_on_path, run_metadata_command
from nk_cli.reclaim import _is_blocked

CONFIG_NAME = ".nk-cli.json"
CONFIG_VERSION = "nk-repository/v1"
METADATA_LIMIT = 1024 * 1024


def git(repo: Path, *args: str) -> bytes:
    repo = repo.expanduser().resolve()
    executable = executable_on_path("git", repo=repo)
    if executable is None:
        raise ValueError("Git is unavailable outside the selected repository")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return run_metadata_command(
        [executable, "-c", "core.fsmonitor=false", "-C", str(repo), *args],
        env=env, timeout=30, max_bytes=16 * 1024 * 1024,
    )


def repository_root(repo: Path) -> Path:
    try:
        return Path(os.fsdecode(git(repo.expanduser(), "rev-parse", "--show-toplevel").rstrip(b"\n")))
    except (OSError, ValueError) as exc:
        raise ValueError("cannot inspect a Git working tree; check --repo and that Git is installed") from exc


def repository_files(repo: Path, *, untracked: bool = False) -> list[str]:
    args = ["ls-files", "--cached", "-z"]
    if untracked:
        args += ["--others", "--exclude-standard"]
    try:
        return sorted({os.fsdecode(item) for item in git(repo, *args).split(b"\0") if item})
    except (OSError, ValueError) as exc:
        raise ValueError("repository file list cannot be read") from exc


def read_metadata(repo: Path, relative: str) -> str:
    path = repo / relative
    if _is_blocked(path.absolute()):
        raise ValueError(f"protected metadata path: {relative}")
    if not path.resolve().is_relative_to(repo.resolve()):
        raise ValueError(f"metadata leaves repository: {relative}")
    if any(part.is_symlink() for part in (path, *path.parents) if part != repo and repo in part.parents):
        raise ValueError(f"symlinked metadata is not read: {relative}")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"metadata is not a regular file: {relative}")
    with path.open("rb") as source:
        content = source.read(METADATA_LIMIT + 1)
    if len(content) > METADATA_LIMIT:
        raise ValueError(f"metadata exceeds 1 MiB: {relative}")
    return content.decode("utf-8")


def load_profile(repo: Path) -> dict | None:
    path = repo / CONFIG_NAME
    if not path.exists() and not path.is_symlink():
        return None
    try:
        profile = json.loads(read_metadata(repo, CONFIG_NAME))
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError(f"cannot read {CONFIG_NAME} as repository JSON") from exc
    if not isinstance(profile, dict) or profile.get("version") != CONFIG_VERSION:
        raise ValueError(f"unsupported {CONFIG_NAME} version")
    for field in ("cache_names", "exclude_paths"):
        values = profile.get(field, [])
        if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
            raise ValueError(f"{CONFIG_NAME}: {field} must be a list of nonempty strings")
    if "tooling" in profile:
        from nk_cli.tooling import validate_tooling_profile
        validate_tooling_profile(profile["tooling"])
    return profile
