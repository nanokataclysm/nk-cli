"""Infer editable settings and task suggestions from bounded, static metadata."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
import tomllib

from nk_cli.repository import CONFIG_NAME, CONFIG_VERSION, read_metadata, repository_files, repository_root

MARKERS = {"package.json", "pyproject.toml", "Cargo.toml", "go.mod", "CMakeLists.txt", "Makefile"}
IGNORED_PARTS = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".next"}
TASK_NAME = re.compile(r"^(test|lint|check|typecheck|build|dev|start)(?:[:._-][A-Za-z0-9:._-]+)?$")
LOCKFILES = {"pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn", "bun.lock": "bun", "bun.lockb": "bun",
             "package-lock.json": "npm", "npm-shrinkwrap.json": "npm"}


def _manager(directory: PurePosixPath, files: set[str], packages: dict) -> tuple[str | None, str]:
    for parent in (directory, *directory.parents):
        package = packages.get(str(parent), {})
        declared = package.get("packageManager")
        if declared is not None:
            name = declared.split("@", 1)[0] if isinstance(declared, str) else ""
            return (name, "packageManager") if name in {"npm", "pnpm", "yarn", "bun"} else (None, "unsupported packageManager")
        managers = {manager for name, manager in LOCKFILES.items() if str(parent / name) in files}
        if len(managers) > 1:
            return None, "conflicting lockfiles"
        if managers:
            return managers.pop(), "lockfile"
    return "npm", "default; no package manager declared"


def _ports(script: str) -> set[int]:
    # Suggestions use explicit numeric declarations only; never read .env files.
    matches = re.findall(r"(?:--port(?:=|\s+)|-p\s+|\bPORT=)([0-9]{1,5})(?=$|[\s;&|])", script)
    return {int(value) for value in matches if 1 <= int(value) <= 65535}


def analyze_repository(repo: Path) -> dict:
    repo = repository_root(repo)
    paths = repository_files(repo, untracked=True)
    files = set(paths)
    warnings: list[str] = []
    metadata: dict[str, object] = {}
    candidates = [p for p in paths if PurePosixPath(p).name in MARKERS
                  and not IGNORED_PARTS.intersection(PurePosixPath(p).parts)]
    if len(candidates) > 1000:
        raise ValueError("more than 1,000 project manifests; analyze a smaller repository")
    for relative in candidates:
        try:
            text = read_metadata(repo, relative)
            suffix = PurePosixPath(relative).suffix
            value = json.loads(text) if suffix == ".json" else tomllib.loads(text) if suffix == ".toml" else text
            if suffix in {".json", ".toml"} and not isinstance(value, dict):
                raise ValueError("metadata must be an object")
            metadata[relative] = value
        except (OSError, ValueError, RuntimeError):
            warnings.append(f"skipped unreadable, invalid, oversized, or symlinked metadata: {relative}")

    packages = {str(PurePosixPath(p).parent): data for p, data in metadata.items()
                if PurePosixPath(p).name == "package.json"}
    projects: dict[str, dict] = {}
    cache_names: set[str] = set()
    services: dict[int, dict] = {}
    for relative, data in metadata.items():
        path = PurePosixPath(relative)
        directory = str(path.parent)
        project = projects.setdefault(directory, {"path": directory, "ecosystems": [], "evidence": [], "tasks": []})
        project["evidence"].append(relative)

        def task(name: str, command: list[str], source: str = "conventional") -> None:
            project["tasks"].append({"name": name, "command": command, "source": source})

        if path.name == "package.json":
            project["ecosystems"].append("node")
            manager, reason = _manager(path.parent, files, packages)
            project["package_manager"] = manager
            project["package_manager_source"] = reason
            if manager is None:
                warnings.append(f"{relative}: {reason}; no package commands suggested")
            scripts = data.get("scripts", {})
            if not isinstance(scripts, dict):
                warnings.append(f"{relative}: scripts must be an object")
                scripts = {}
            for name, script in sorted(scripts.items()):
                if isinstance(script, str) and TASK_NAME.fullmatch(name):
                    if manager:
                        task(name, [manager, "run", name], "declared")
                    if name in {"dev", "start"}:
                        for port in _ports(script):
                            services.setdefault(port, {"id": f"port-{port}", "host": "127.0.0.1", "port": port,
                                                       "required": True})
        elif path.name == "pyproject.toml":
            project["ecosystems"].append("python")
            tools = data.get("tool", {})
            if isinstance(tools, dict):
                if "pytest" in tools:
                    task("test", ["python", "-m", "pytest"])
                if "ruff" in tools:
                    task("lint", ["ruff", "check", "."])
                if "mypy" in tools:
                    task("typecheck", ["mypy", "."])
            # A test directory alone does not identify pytest versus unittest.
            if not project["tasks"]:
                warnings.append(f"{relative}: no supported test/lint tool configuration; no Python task guessed")
        elif path.name == "Cargo.toml":
            project["ecosystems"].append("rust")
            for name in ("test", "build", "check"):
                task(name, ["cargo", name])
            cache_names.add("target")
        elif path.name == "go.mod":
            project["ecosystems"].append("go")
            for name in ("test", "build"):
                task(name, ["go", name, "./..."])
        elif path.name == "CMakeLists.txt":
            project["ecosystems"].append("cmake")
            task("configure", ["cmake", "-S", ".", "-B", "build"])
            task("build", ["cmake", "--build", "build"])
        elif path.name == "Makefile":
            project["ecosystems"].append("make")
            for name in sorted(set(re.findall(r"^([A-Za-z][A-Za-z0-9_.-]*):(?!=)", data, re.MULTILINE))):
                if TASK_NAME.fullmatch(name):
                    task(name, ["make", name], "declared")

    config = {
        "version": CONFIG_VERSION,
        "root_files": sorted({p for p in paths if "/" not in p} | {CONFIG_NAME}),
        "units": sorted({p.split("/")[0] for p in paths if "/" in p}),
        "allow_tracked": [], "cache_names": sorted(cache_names), "exclude_paths": [],
        "services": [services[p] for p in sorted(services)],
    }
    return {"repo": str(repo), "projects": list(projects.values()), "warnings": warnings,
            "suggested_config": config}


def write_profile(report: dict) -> Path:
    destination = Path(report["repo"]) / CONFIG_NAME
    # Exclusive creation preserves both user edits and pre-existing symlinks.
    with destination.open("x", encoding="utf-8") as output:
        output.write(json.dumps(report["suggested_config"], indent=2, ensure_ascii=True) + "\n")
    return destination
