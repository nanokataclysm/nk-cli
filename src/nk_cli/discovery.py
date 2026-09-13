"""Small, bounded helpers for inspecting installed tools and local metadata."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading


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
    extensions = [""]
    if os.name == "nt":
        extensions += os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
    for entry in os.environ.get("PATH", "").split(os.pathsep)[:128]:
        directory = Path(entry)
        if not entry or not directory.is_absolute():
            continue
        try:
            directory = directory.resolve()
            if any(directory.is_relative_to(root) for root in excluded):
                continue
            for extension in extensions:
                candidate = directory / (name + extension)
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
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=cwd or tempfile.gettempdir(), env=env,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise ValueError("metadata command could not start") from exc

    content = bytearray()
    oversized = threading.Event()
    read_error = threading.Event()

    def stop() -> None:
        try:
            if os.name != "nt":
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
