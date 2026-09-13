"""Inventory local executables and explicit model preferences without running agents."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any

from nk_cli.discovery import executable_at_path, executable_on_path, run_metadata_command


KNOWN_TOOLS = (
    "aichat", "aider", "amp", "claude", "codex", "continue", "copilot",
    "crush", "gemini", "goose", "gptme", "interpreter", "llm", "lms",
    "ollama", "opencode", "openhands", "pi", "qwen",
)
_TOOL_PATTERN = re.compile(r"(?:agent[-_].+|llm[-_].+|model[-_].+|.+[-_]agent|.+[-_]llm)", re.I)
_SYSTEM_HELPERS = {"ssh-agent", "gpg-agent", "polkit-agent", "systemd-ssh-agent", "systemd-tty-ask-password-agent"}
_MAX_PREFERENCES = 64
_MAX_PATH_DIRS = 128
_MAX_PATH_ENTRIES = 8192
_MAX_MODELS = 256


def _strings(value: object, *, field: str, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > _MAX_PREFERENCES:
        raise ValueError(f"tooling {field} must contain at most {_MAX_PREFERENCES} strings")
    result = []
    for item in value:
        if (not isinstance(item, str) or not item or len(item) > limit
                or item.strip() != item or not item.isprintable()):
            raise ValueError(f"tooling {field} requires nonempty printable strings of at most {limit} characters")
        if item not in result:
            result.append(item)
    return result


def validate_tooling_profile(value: object) -> dict[str, list[str]]:
    """Validate portable preferences; profiles contain lists, never commands or prompts."""
    if not isinstance(value, dict) or set(value) - {"agents", "models"}:
        raise ValueError("tooling must be an object containing only agents and models")
    result = {}
    for field, limit in (("agents", 512), ("models", 256)):
        items = value.get(field, [])
        if not isinstance(items, list):
            raise ValueError(f"tooling {field} must be a list")
        result[field] = _strings(items, field=field, limit=limit)
    return result


def _inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _path_names(repo: Path | None, warnings: list[str]) -> set[str]:
    """Find suggestive executable names, bounded and excluding working-tree PATH dirs."""
    names = set(KNOWN_TOOLS)
    roots = [Path.cwd().resolve()]
    if repo is not None:
        roots.append(repo.resolve())
    for directory in tuple(roots):
        for ancestor in (directory, *directory.parents):
            if (ancestor / ".git").exists():
                roots.append(ancestor)
                break
    seen = set()
    remaining = _MAX_PATH_ENTRIES
    directories = os.environ.get("PATH", "").split(os.pathsep)
    if len(directories) > _MAX_PATH_DIRS:
        warnings.append("PATH discovery stopped after 128 directory entries.")
    for raw in directories[:_MAX_PATH_DIRS]:
        directory = Path(raw)
        if not raw or not directory.is_absolute():
            continue
        try:
            directory = directory.resolve()
            if directory in seen or any(_inside(directory, root) for root in roots):
                continue
            seen.add(directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    if remaining <= 0:
                        warnings.append("PATH name discovery reached its entry limit; use --agent for additional tools.")
                        return names
                    remaining -= 1
                    name = entry.name
                    if os.name == "nt":
                        name = name.lower()
                        for suffix in (".exe", ".cmd", ".bat", ".com"):
                            if name.endswith(suffix):
                                name = name[:-len(suffix)]
                                break
                    if (len(name) <= 512 and name.isprintable() and name.lower() not in _SYSTEM_HELPERS
                            and _TOOL_PATTERN.fullmatch(name)):
                        names.add(name)
        except (OSError, RuntimeError):
            # An inaccessible PATH directory does not prevent checking the known names.
            continue
    return names


def _selected_executable(selector: str, repo: Path | None) -> str | None:
    if "/" not in selector and "\\" not in selector:
        return executable_on_path(selector, repo=repo)
    path = Path(selector).expanduser()
    if not path.is_absolute():
        path = (repo if repo is not None else Path.cwd()) / path
    return executable_at_path(path)


def _ollama_names(output: bytes) -> list[str]:
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValueError("invalid model catalog encoding") from exc
    if not lines or lines[0].split() != ["NAME", "ID", "SIZE", "MODIFIED"]:
        raise ValueError("unsupported model catalog header")
    names = []
    row = re.compile(r"([A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255})\s+[0-9a-fA-F]{12,64}\s+\d+(?:\.\d+)?\s+(?:B|KB|MB|GB|TB|PB)\s+.+")
    for line in lines[1:]:
        if not line.strip():
            continue
        if not line.isprintable() or not (match := row.fullmatch(line.strip())):
            raise ValueError("unsupported model catalog row")
        if match[1] not in names:
            names.append(match[1])
        if len(names) > _MAX_MODELS:
            raise ValueError("model catalog exceeds entry limit")
    return names


def discover_tools(
    repo: Path | None = None,
    *,
    agents: tuple[str, ...] = (),
    models: tuple[str, ...] = (),
    list_models: bool = False,
    profile: dict | None = None,
) -> dict[str, Any]:
    """Report installed candidates and preferences, never choose or invoke an agent.

    Explicit nonempty selections replace the corresponding profile list. Missing
    preferences remain selected and unavailable; PATH candidates are not fallbacks.
    Model listing is separately opt-in and queries only the local Ollama inventory.
    """
    saved = validate_tooling_profile(profile) if profile is not None else {"agents": [], "models": []}
    specified_agents = _strings(agents, field="agents", limit=512)
    specified_models = _strings(models, field="models", limit=256)
    selected_agents = specified_agents or saved["agents"]
    selected_models = specified_models or saved["models"]
    agent_source = "specified" if specified_agents else "profile" if saved["agents"] else "none"
    model_source = "specified" if specified_models else "profile" if saved["models"] else "none"
    warnings: list[str] = []
    inventory = {}
    for name in sorted(_path_names(repo, warnings)):
        executable = executable_on_path(name, repo=repo)
        if executable is not None:
            inventory[name] = {
                "id": name, "executable": executable, "available": True, "source": "path", "selected": False,
                "evidence": "known-tool" if name in KNOWN_TOOLS else "name-match-unverified",
            }
    for selector in selected_agents:
        executable = _selected_executable(selector, repo)
        inventory[selector] = {
            "id": selector, "executable": executable, "available": executable is not None,
            "source": agent_source, "selected": True, "evidence": "user-preference",
        }
        if executable is None:
            warnings.append(f"Selected agent executable is unavailable: {selector}")
    if any(item["evidence"] == "name-match-unverified" for item in inventory.values()):
        warnings.append("Some executable candidates match agent/model names only; their capabilities are unverified.")

    model_inventory = {
        name: {"id": name, "source": model_source, "status": "specified-unverified", "verified": False, "selected": True}
        for name in selected_models
    }
    catalog = {"requested": bool(list_models), "runtime": "ollama", "status": "not-requested"}
    if list_models:
        executable = executable_on_path("ollama", repo=repo)
        if executable is None:
            catalog["status"] = "unavailable"
            warnings.append("Local model catalog unavailable: Ollama is not on the usable PATH. Explicit --model IDs remain accepted and unverified.")
        else:
            # Ignore a configured remote Ollama endpoint and all ambient proxies.
            env = {key: value for key, value in os.environ.items() if key.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
            env.update(OLLAMA_HOST="http://127.0.0.1:11434", NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1", LC_ALL="C", NO_COLOR="1", TERM="dumb")
            try:
                names = _ollama_names(run_metadata_command([executable, "list"], env=env))
            except (ValueError, OSError):
                catalog["status"] = "unavailable"
                warnings.append("Local Ollama model inventory could not be read or had an unsupported format. Explicit --model IDs remain accepted and unverified.")
            else:
                catalog["status"] = "listed-local"
                for name in names:
                    if name in model_inventory:
                        model_inventory[name]["catalog_status"] = "listed-local"
                        model_inventory[name]["runtime"] = "ollama"
                    else:
                        model_inventory[name] = {"id": name, "source": "ollama-local", "status": "listed-local", "runtime": "ollama", "verified": False, "selected": False}
    return {
        "agents": [inventory[name] for name in sorted(inventory)],
        "models": [model_inventory[name] for name in sorted(model_inventory)],
        "selected_agents": selected_agents,
        "selected_models": selected_models,
        "selection_source": {"agents": agent_source, "models": model_source},
        "model_catalog": catalog,
        "warnings": warnings,
    }
