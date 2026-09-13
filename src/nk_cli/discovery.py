"""Small, bounded helpers for inspecting installed tools and local metadata."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading


_WINDOWS = os.name == "nt"
_WINDOWS_SUFFIXES = (".com", ".exe", ".bat", ".cmd")


def _executable_names(name: str) -> tuple[str, ...]:
    """Expand Windows launchers without accepting extensionless POSIX shims."""
    if not _WINDOWS:
        return (name,)
    if Path(name).suffix.lower() in _WINDOWS_SUFFIXES:
        return (name,)
    # PATHEXT can contain arbitrary file associations, paths, or empty entries.
    # Only native programs and the batch launchers we inventory are supported.
    raw_extensions = os.environ.get("PATHEXT", ";".join(_WINDOWS_SUFFIXES))
    extensions = dict.fromkeys(
        extension.strip().lower()
        for extension in raw_extensions[:2048].split(";")[:64]
        if extension.strip().lower() in _WINDOWS_SUFFIXES
    )
    return tuple(name + extension for extension in extensions)


def executable_at_path(path: Path) -> str | None:
    """Resolve an explicitly selected path using the platform's launcher rules."""
    for name in _executable_names(path.name):
        try:
            candidate = path.with_name(name).resolve()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except (OSError, RuntimeError):
            continue
    return None


def executable_on_path(name: str, *, repo: Path | None = None) -> str | None:
    """Find a command without implicit cwd lookup or repository PATH shims."""
    if not name or Path(name).name != name or "/" in name or "\\" in name:
        return None
    excluded = {Path.cwd().resolve()}
    if repo is not None:
        excluded.add(repo.resolve())
    for directory in tuple(excluded):
        for parent in (directory, *directory.parents):
            if (parent / ".git").exists():
                excluded.add(parent)
                break
    for entry in os.environ.get("PATH", "").split(os.pathsep)[:128]:
        directory = Path(entry)
        if not entry or not directory.is_absolute():
            continue
        try:
            directory = directory.resolve()
            if any(directory.is_relative_to(root) for root in excluded):
                continue
            for filename in _executable_names(name):
                candidate = directory / filename
                resolved = candidate.resolve()
                if any(resolved.is_relative_to(root) for root in excluded):
                    continue
                if resolved.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
        except (OSError, RuntimeError):
            continue
    return None


def run_metadata_command(
    argv: list[str], *, cwd: Path | None = None,
    env: dict[str, str] | None = None, timeout: float = 3,
    max_bytes: int = 1024 * 1024,
) -> bytes:
    """Read bounded stdout, discard diagnostics, and never invoke a shell."""
    if (not argv or not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in argv)
            or timeout <= 0 or max_bytes <= 0):
        raise ValueError("invalid metadata command or limits")
    if _WINDOWS and Path(argv[0]).suffix.lower() not in {".exe", ".com"}:
        # Windows may dispatch batch files through cmd.exe even with shell=False.
        # Discovery can report those launchers, but metadata probes never run them.
        raise ValueError("metadata command requires a native Windows executable")
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=cwd or tempfile.gettempdir(), env=env,
            start_new_session=not _WINDOWS,
        )
    except OSError as exc:
        raise ValueError("metadata command could not start") from exc

    content = bytearray()
    oversized = threading.Event()
    read_error = threading.Event()

    def stop() -> None:
        try:
            if not _WINDOWS:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            pass

    def read() -> None:
        try:
            while chunk := process.stdout.read1(65536):
                if len(content) + len(chunk) > max_bytes:
                    oversized.set()
                    stop()
                    break
                content.extend(chunk)
        except OSError:
            read_error.set()
        finally:
            process.stdout.close()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
        reader.join(timeout=0.5)
        if reader.is_alive():
            stop()
            raise ValueError("metadata command output did not close")
    except subprocess.TimeoutExpired as exc:
        stop()
        process.wait(timeout=1)
        reader.join(timeout=0.5)
        raise ValueError("metadata command timed out") from exc
    if oversized.is_set():
        raise ValueError("metadata command output exceeds limit")
    if read_error.is_set() or process.returncode:
        raise ValueError("metadata command failed")
    return bytes(content)
