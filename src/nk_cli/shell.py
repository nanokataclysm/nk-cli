"""Render task suggestions for the user's shell without executing them."""

from __future__ import annotations

import os
import shlex


def command_shell(shell: str = "auto") -> str:
    if shell == "auto":
        return "powershell" if os.name == "nt" else "posix"
    if shell not in {"posix", "powershell"}:
        raise ValueError("unsupported command shell")
    return shell


def format_command(argv: list[str], *, shell: str = "auto") -> str:
    if not argv or not all(isinstance(arg, str) and "\x00" not in arg for arg in argv):
        raise ValueError("command must contain valid string arguments")
    if command_shell(shell) == "powershell":
        # Windows PowerShell 5.1 changes these during native argument binding.
        # Keep the JSON argument array authoritative instead of printing a
        # command that would silently lose or alter an argument.
        if any(not arg or '"' in arg or "\n" in arg or "\r" in arg for arg in argv):
            raise ValueError("use JSON arguments for empty, quoted, or multiline PowerShell values")
        if argv[0].lower().endswith((".cmd", ".bat")) and any(
            any(char in arg for char in "%!^&|<>") for arg in argv[1:]
        ):
            raise ValueError("use JSON arguments for batch-shell metacharacters")
        # The call operator is required for a quoted executable path. Single
        # quotes keep PowerShell expressions literal; apostrophes double.
        return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)
    return shlex.join(argv)
