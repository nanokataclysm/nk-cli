"""Repository analysis, boundary checks, local diagnostics, and cache reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nk_cli import __version__
from nk_cli.analyze import analyze_repository, write_profile
from nk_cli.boundaries import boundary_report
from nk_cli.discovery import executable_on_path
from nk_cli.network import discover_targets
from nk_cli.portal_doctor import inspect, load_manifest
from nk_cli.reclaim import as_jsonable, format_human, scan
from nk_cli.repository import CONFIG_NAME, load_profile, repository_root
from nk_cli.shell import command_shell, format_command
from nk_cli.tooling import discover_tools, validate_tooling_profile


def _optional_repo(directory: Path) -> Path | None:
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise ValueError("selected directory does not exist")
    try:
        return repository_root(directory)
    except ValueError:
        return None


def _tools_for(args: argparse.Namespace, repo: Path | None, profile: dict) -> dict:
    return discover_tools(repo, agents=tuple(args.agent), models=tuple(args.model),
                          list_models=args.list_models, profile=profile.get("tooling"))


def _targets_for(args: argparse.Namespace, repo: Path | None) -> dict:
    return discover_targets(repo, targets=tuple(args.target), lan=args.lan,
                            tailscale=args.tailscale, probe=args.probe)


def _print_tools(report: dict) -> None:
    print("\nAgents and model runtimes:")
    for agent in report["agents"]:
        print(f"  {agent['id']}: {agent['executable'] if agent['available'] else 'unavailable'} [{agent['source']}]")
    if not report["agents"]:
        print("  None found; use --agent COMMAND to specify an installed tool.")
    for model in report["models"]:
        print(f"  model {model['id']} [{model['source']}]")
    print("  Executable presence and model names do not establish authentication or inference readiness.")
    for warning in report["warnings"]:
        print(f"Note: {warning}")


def _print_targets(report: dict) -> None:
    print("\nPush target candidates:")
    for target in report["targets"]:
        host = f"[{target['host']}]" if ":" in target["host"] else target["host"]
        print(f"  {target['id']}: {host}:{target['port']} [{target['network']}; {target['source']}; {target['reachability']}]")
    if not report["targets"]:
        print("  None found; use --target HOST, --lan, or --tailscale to add discovery sources.")
    print("  Candidates require destination and access verification before pushing.")
    for warning in report["warnings"]:
        print(f"Note: {warning}")


def _cmd_analyze(args: argparse.Namespace) -> int:
    report = analyze_repository(args.repo)
    report["command_shell"] = command_shell(args.shell)
    repo = Path(report["repo"])
    profile = load_profile(repo) or {}
    report["tooling"] = _tools_for(args, repo, profile)
    report["push_targets"] = _targets_for(args, repo)
    preferences = validate_tooling_profile(profile.get("tooling", {}))
    report["suggested_config"]["tooling"] = {
        "agents": args.agent or preferences["agents"],
        "models": args.model or preferences["models"],
    }
    for project in report["projects"]:
        for task in project["tasks"]:
            name = task["command"][0]
            executable = executable_on_path(name, repo=repo)
            if name == "python" and executable is None:
                executable = executable_on_path("python3", repo=repo)
            task["executable_available"] = executable is not None
            task["resolved_executable"] = executable
            if executable:
                task["command"][0] = executable
    if args.write_config:
        report["config_written"] = str(write_profile(report))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Repository: {report['repo']}")
        print(f"Task suggestions use {report['command_shell']} syntax.")
        for project in report["projects"]:
            print(f"\n{project['path']} — {', '.join(project['ecosystems'])}")
            for task in project["tasks"]:
                available = "executable found" if task["executable_available"] else "executable missing"
                try:
                    command = format_command(task["command"], shell=report["command_shell"])
                except ValueError:
                    command = "JSON arguments (not a shell command): " + json.dumps(task["command"])
                print(f"  {task['name']}: {command} [{task['source']}; {available}]")
        if not report["projects"]:
            print("No supported project manifests found; boundary and cache checks still work.")
        for warning in report["warnings"]:
            print(f"Note: {warning}")
        _print_tools(report["tooling"])
        _print_targets(report["push_targets"])
        print("\nTask commands are suggestions in each project's directory; none were executed.")
        if args.write_config:
            print(f"Created {report['config_written']}; review and edit the repository settings.")
        else:
            print(f"Use --json to inspect suggested settings, or --write-config to create {CONFIG_NAME}.")
    return 0


def _cmd_tools(args: argparse.Namespace) -> int:
    repo = _optional_repo(args.repo)
    profile = (load_profile(repo) or {}) if repo else {}
    report = _tools_for(args, repo, profile)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_tools(report)
    return 0 if all(agent["available"] for agent in report["agents"]
                    if agent["id"] in report["selected_agents"]) else 1


def _cmd_targets(args: argparse.Namespace) -> int:
    report = _targets_for(args, _optional_repo(args.repo))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_targets(report)
    return 0


def _cmd_boundaries(args: argparse.Namespace) -> int:
    code, report = boundary_report(args.repo, args.manifest, allow_patterns=tuple(args.allow))
    if args.json:
        print(json.dumps(report, indent=2))
    elif code:
        for error in report["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
    else:
        print(f"repository-boundaries: clean ({report['tracked_files']} tracked files; {report['mode']} mode)")
    return code


def _cmd_doctor(args: argparse.Namespace) -> int:
    if args.host != "127.0.0.1" and not args.port:
        raise ValueError("--host requires --port; saved services specify their own addresses")
    if args.manifest:
        manifest = load_manifest(args.manifest)
    elif args.port:
        manifest = {"version": "nk-services/v1", "services": [
            {"id": f"port-{port}", "port": port, "host": args.host} for port in dict.fromkeys(args.port)
        ]}
    else:
        repo = repository_root(args.repo)
        manifest = load_profile(repo)
        if manifest is None:
            manifest = analyze_repository(repo)["suggested_config"]
    results = inspect(manifest)
    payload = [host.to_dict() for host in results]
    if args.json:
        if args.command == "portal-doctor":
            print(json.dumps({"hosts": payload}, indent=2))
        else:
            services = [{key: value for key, value in item.items() if key not in {"role", "recovery"}}
                        for item in payload]
            print(json.dumps({"services": services}, indent=2))
    else:
        for host in results:
            print(f"{host.id:20}  {host.status:14}  {'required' if host.required else 'optional'}")
            for check in host.checks:
                print(f"  - {check.check}: {check.status} — {check.detail}")
    return 0 if all(h.status == "verified" for h in results if h.required) else 1


def _cmd_reclaim(args: argparse.Namespace) -> int:
    root = args.root or Path.cwd()
    try:
        repo = repository_root(root)
    except ValueError:
        repo = root.expanduser().resolve()
    if args.root is None:
        root = repo
    profile = load_profile(repo) or {}
    candidates = scan(
        root, max_depth=args.depth, max_files=args.max_files,
        include_names=tuple(profile.get("cache_names", [])) + tuple(args.include),
        exclude_paths=tuple(profile.get("exclude_paths", [])) + tuple(args.exclude),
        exclude_base=repo,
    )
    if args.json:
        print(json.dumps({"dry_run": True, "candidates": as_jsonable(candidates)}, indent=2))
    else:
        print(format_human(candidates))
    return 0


def _tool_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent", action="append", default=[], metavar="COMMAND", help="select any agent/runtime executable by name or path; repeatable")
    parser.add_argument("--model", action="append", default=[], metavar="ID", help="specify model IDs without assuming provider availability; repeatable")
    parser.add_argument("--list-models", action="store_true", help="query supported local model catalogs (Ollama loopback); never generates")


def _target_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", action="append", default=[], metavar="HOST_OR_URL", help="explicit SSH/HTTP/Git candidate; repeatable")
    parser.add_argument("--lan", action="store_true", help="include the OS neighbor cache; no subnet scan")
    parser.add_argument("--tailscale", action="store_true", help="include peers reported by local tailscale status")
    parser.add_argument("--probe", action="store_true", help="try bounded TCP connections to discovered candidates; no authentication or transfer")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nk-cli",
        description="Adapt repository checks and local diagnostics to your project.",
    )
    parser.add_argument("--version", action="version", version=f"nk-cli {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyze", help="discover project metadata and suggest repository settings")
    a.add_argument("--repo", type=Path, default=Path.cwd(), help="repository or subdirectory (default: current directory; discovers Git root)")
    a.add_argument("--write-config", action="store_true", help="create .nk-cli.json; never overwrites")
    a.add_argument("--json", action="store_true", help="machine-readable analysis and suggested configuration")
    a.add_argument("--shell", choices=["auto", "posix", "powershell"], default="auto", help="task suggestion syntax (default: PowerShell on Windows, POSIX elsewhere)")
    _tool_options(a)
    _target_options(a)
    a.set_defaults(func=_cmd_analyze)

    t = sub.add_parser("tools", help="discover installed agents/runtimes and specified or local models")
    t.add_argument("--repo", type=Path, default=Path.cwd(), help="repository for saved tool preferences (default: current directory)")
    t.add_argument("--json", action="store_true", help="machine-readable tool inventory")
    _tool_options(t)
    t.set_defaults(func=_cmd_tools)

    n = sub.add_parser("targets", help="discover Git and SSH push candidates across LAN/WAN/Tailscale")
    n.add_argument("--repo", type=Path, default=Path.cwd(), help="repository for Git remotes (default: current directory)")
    n.add_argument("--json", action="store_true", help="machine-readable target inventory")
    _target_options(n)
    n.set_defaults(func=_cmd_targets)

    b = sub.add_parser("boundaries", help="check tracked artifacts and optional directory boundaries")
    b.add_argument("--repo", type=Path, default=Path.cwd(), help="repository or subdirectory (default: cwd)")
    b.add_argument("--manifest", type=Path, help="explicit manifest; otherwise uses .nk-cli.json if present")
    b.add_argument("--allow", action="append", default=[], metavar="GLOB", help="allow an intentional tracked artifact; repeatable")
    b.add_argument("--json", action="store_true", help="machine-readable inventory and findings")
    b.set_defaults(func=_cmd_boundaries)

    p = sub.add_parser("doctor", aliases=["portal-doctor"], help="check configured local TCP listeners")
    p.add_argument("--repo", type=Path, default=Path.cwd(), help="repository for inferred or saved service settings")
    targets = p.add_mutually_exclusive_group()
    targets.add_argument("--manifest", type=Path, help="service JSON (legacy host manifests also accepted)")
    targets.add_argument("--port", action="append", type=int, help="check this port; repeatable")
    p.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1", help="address for --port; localhost means 127.0.0.1")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=_cmd_doctor)

    r = sub.add_parser("reclaim", help="dry-run disk reclaim report (never deletes)")
    r.add_argument("--root", type=Path, help="scan root (default: Git root, or cwd outside Git)")
    r.add_argument("--depth", type=int, default=6, help="max directory depth (default: 6)")
    r.add_argument("--json", action="store_true", help="machine-readable output")
    r.add_argument("--include", action="append", default=[], metavar="NAME", help="additional cache directory name, e.g. target; repeatable")
    r.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip a repository-relative path pattern; repeatable")
    r.add_argument("--max-files", type=int, default=50_000, help="per-candidate size scan limit (default: 50000)")
    r.set_defaults(func=_cmd_reclaim)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"errors": [str(exc)]}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
