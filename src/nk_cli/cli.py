"""nk-cli entrypoint — public-safe assistive utilities only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nk_cli import __version__
from nk_cli.boundaries import run_boundaries
from nk_cli.portal_doctor import ManifestError, inspect, load_manifest
from nk_cli.reclaim import as_jsonable, format_human, scan


def _cmd_boundaries(args: argparse.Namespace) -> int:
    code, lines = run_boundaries(args.repo, args.manifest)
    for line in lines:
        print(line if code == 0 else f"ERROR: {line}")
    return code


def _cmd_portal_doctor(args: argparse.Namespace) -> int:
    try:
        manifest = load_manifest(args.manifest)
        results = inspect(manifest)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    payload = [host.to_dict() for host in results]
    if args.json:
        print(json.dumps({"hosts": payload}, indent=2))
    else:
        for host in results:
            print(f"{host.id:20}  {host.status:14}  role={host.role}  recovery={host.recovery}")
            for check in host.checks:
                print(f"  - {check.check}: {check.status} — {check.detail}")
    worst = max((h.status for h in results), key=lambda s: {"verified": 0, "unknown": 1, "offline": 2, "logged-out": 3, "misconfigured": 4}.get(s, 1), default="verified")
    return 0 if worst in {"verified", "unknown"} else 1


def _cmd_reclaim(args: argparse.Namespace) -> int:
    try:
        candidates = scan(args.root, max_depth=args.depth)
    except (FileNotFoundError, PermissionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"dry_run": True, "candidates": as_jsonable(candidates)}, indent=2))
    else:
        print(format_human(candidates))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nk-cli",
        description="Public-safe assistive utilities (not the private host-control nk CLI).",
    )
    parser.add_argument("--version", action="version", version=f"nk-cli {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("boundaries", help="validate monorepo unit manifest vs git tracked files")
    b.add_argument("--repo", type=Path, default=Path.cwd(), help="git repository root (default: cwd)")
    b.add_argument("--manifest", type=Path, required=True, help="path to units manifest JSON")
    b.set_defaults(func=_cmd_boundaries)

    p = sub.add_parser("portal-doctor", help="read-only portal/host checks from a local manifest")
    p.add_argument("--manifest", type=Path, required=True, help="path to portal hosts JSON")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=_cmd_portal_doctor)

    r = sub.add_parser("reclaim", help="dry-run disk reclaim report (never deletes)")
    r.add_argument("--root", type=Path, default=Path.cwd(), help="scan root (default: cwd)")
    r.add_argument("--depth", type=int, default=6, help="max directory depth (default: 6)")
    r.add_argument("--json", action="store_true", help="machine-readable output")
    r.set_defaults(func=_cmd_reclaim)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
