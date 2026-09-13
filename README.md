# nk-cli

Repository analysis, installed tool discovery, push-target candidates, and local checks.
Works with single projects and monorepos. Requires Python 3.11+ and Git;
there are no third-party Python runtime dependencies.
The same Python package supports Linux, macOS, and Windows.

## Start with your repository

From an installed `nk-cli`, run:

```sh
cd /path/to/your/repository
nk-cli analyze                       # analyze the repository you are in
nk-cli tools                         # installed agents/runtimes and saved preferences
nk-cli targets --lan --tailscale      # Git remotes, cached neighbors, and tailnet peers
nk-cli boundaries
nk-cli reclaim --json
```

`analyze` reads project metadata and suggests tasks with their working
directories. `boundaries` checks tracked runtime artifacts immediately, without
a manifest. With no `--repo`, analysis starts from your current working directory.
Commands locate the Git root when started in a subdirectory and support linked
Git worktrees. The current directory must be inside a Git working tree for analysis.
You can also select the directory explicitly or choose another checkout:

```sh
nk-cli analyze --repo .               # same as nk-cli analyze
nk-cli analyze --repo /path/to/repo   # analyze another checkout
```

To save the detected settings for subsequent checks:

```sh
nk-cli analyze --json                 # inspect the full proposed profile
nk-cli analyze --write-config         # create .nk-cli.json; refuses overwrite
nk-cli boundaries                    # now enforces the saved directory rules
nk-cli doctor                        # use saved local services, if configured
```

Review the generated profile before checking it in. It contains repository-relative
paths, directory rules, additional cache names, and explicitly declared local
ports. Edit it as the repository changes. Analysis never rewrites application
code, executes task scripts, installs dependencies, or reads `.env` files.

## Tools and models

```sh
nk-cli tools --json
nk-cli tools --agent my-agent --model my-model
nk-cli tools --agent /path/to/custom-agent --model organization/model
nk-cli tools --list-models
nk-cli analyze --agent my-agent --model my-model --write-config
```

Discovery checks executable files on `PATH` without launching agents. It recognizes
common CLI names and agent/LLM/model naming patterns; `--agent` accepts any other
executable name or explicit path. A missing selected tool stays visibly unavailable.
Explicit selections override saved preferences without choosing a replacement
provider. Model IDs are preferences, not proof of API access or compatibility.
No hardcoded model catalog, host inventory, or persona is required.

`--list-models` optionally reads Ollama's installed-model list from
`127.0.0.1:11434`. It does not inherit a remote `OLLAMA_HOST` value. Other catalogs
are not queried; use `--model` to specify them. No inference, model downloads,
login, or credential inspection occurs.

`analyze` resolves task executables against the available PATH, using `python3`
when `python` is absent. Missing tools remain labeled; declared package managers
are not silently replaced. Executable presence does not prove dependencies are
installed or the suggested task will succeed.

Task suggestions use POSIX shell syntax on Linux/macOS and PowerShell syntax on
Windows, including quoted executable paths and npm-style `.cmd` launchers.
Use `analyze --shell posix` in Git Bash or `--shell powershell` to choose explicitly.
`--json` always retains argument arrays. Arguments that older PowerShell/native
binding cannot preserve are shown as JSON rather than an inaccurate shell command.
Windows discovery follows supported `PATHEXT` entries and skips extensionless
POSIX shims. `.cmd`/`.bat` agents can be inventoried, but metadata probes require
native `.exe`/`.com` tools.

Only explicit/saved tool preferences enter `.nk-cli.json` under
`"tooling": {"agents": ["my-agent"], "models": ["my-model"]}`. Detected absolute
paths and model catalogs stay in the report. Prefer command names for portable
profiles; explicit paths are machine-specific, and relative agent paths resolve
against the selected Git root. Repository-local PATH shims and relative PATH
entries are excluded from automatic discovery. An explicit `--agent` path can
identify a local tool, but still does not execute it.

## Push target discovery

```sh
nk-cli targets                                      # configured Git remote endpoints
nk-cli targets --lan --tailscale                     # add passive discovery sources
nk-cli targets --target ssh://build-box:2222          # explicit SSH candidate
nk-cli targets --target https://git.example.org/repo # explicit WAN candidate
nk-cli targets --target 192.168.1.20:22 --probe       # optional TCP listener check
nk-cli analyze --lan --tailscale --json              # combined repository report
```

Both Git remotes and SSH transfer/deploy hosts are candidates. Git discovery
honors local `pushurl` settings, including multiple push URLs. It reads literal
local settings, not URL rewrites, included/global Git settings, or SSH
Host/ProxyJump configuration. Remote userinfo, repository paths, and URL queries
are omitted from reports. Local filesystem Git remotes are skipped.

LAN discovery reads the OS neighbor cache; it does not sweep subnets. Linux uses
IPv4/IPv6 neighbor records, while macOS/Windows use cached IPv4 ARP records.
Tailscale discovery reads local `tailscale status --json` and does not log in or
change routing. A peer being online does not establish SSH access. Port 22 on
observed neighbors/peers is a conventional candidate, not a detected service.

WAN candidates come from configured or explicit endpoints; there is no public
Internet scan. Hostnames stay unclassified until `--probe` resolves them. CGNAT
addresses alone are not treated as Tailscale peers. Missing discovery tools produce
diagnostics while other sources remain usable.

At most 128 candidates are reported. `--probe` checks at most 16 distinct endpoints,
with a 0.6-second deadline per endpoint, including bounded DNS waiting. It proves
only TCP reachability. No SSH authentication, Git push, file transfer, deployment,
or agent invocation occurs. Confirm access and the destination repository/directory
before using a candidate. Network observations are never saved in `.nk-cli.json`.

## Project adaptation

| Metadata | Suggestions |
|---|---|
| `package.json` | Declared test/lint/check/typecheck/build/dev/start scripts, including names such as `test:unit` |
| Package-manager declaration or lockfile | npm, pnpm, Yarn, or Bun; nested projects inherit the nearest available selection |
| `pyproject.toml` | pytest, Ruff, and mypy commands when their tool configuration is present |
| `Cargo.toml` | Conventional Cargo test/build/check commands and `target` cache inclusion in the proposed profile |
| `go.mod` | Conventional Go test/build commands |
| `CMakeLists.txt` | Conventional configure/build commands using a `build` directory |
| `Makefile` | Recognized literal test/lint/check/build/dev/start targets |

Suggestions are labeled **declared** or **conventional**. They are not proof that
a tool is installed or a task succeeds. Inspect repository scripts before running
them: a script named `test` can execute arbitrary code. nk-cli does not run these
suggestions. Conflicting lockfiles produce a diagnostic instead of choosing a
package manager; npm is an explicitly labeled default when no selection exists.

Analysis includes tracked and untracked, non-ignored metadata. It skips dependency
trees, protected paths, symlinks, malformed files, and files larger than 1 MiB,
and stops above 1,000 recognized manifests. Unsupported build systems still get
the general boundary/cache checks; no test framework is guessed from a directory
name alone. Resolved executable paths reflect the analysis environment; rerun
analysis after changing machines or environments.

## Repository profile

An example `.nk-cli.json` for a web application and a Python service:

```json
{
  "version": "nk-repository/v1",
  "root_files": ["README.md", ".gitignore", ".nk-cli.json"],
  "units": ["apps/web", "services/api", "docs", ".github"],
  "allow_tracked": ["fixtures/node_modules/*"],
  "cache_names": ["target"],
  "exclude_paths": ["fixtures", "vendor"],
  "services": [
    {"id": "web", "host": "127.0.0.1", "port": 4100, "required": true},
    {"id": "api", "host": "127.0.0.1", "port": 8000, "required": false}
  ]
}
```

Customize this example to match your actual files. Generated `units` are a
snapshot of top-level directories. Narrow a unit to `apps/web` when you want
strict nested boundaries: that declaration does **not** authorize `apps/api`.
The tool checks directory coverage and tracked artifacts, not imports or
architectural dependency direction. Legacy unit objects with a `path` remain
accepted; kind/lifecycle/deployment metadata is no longer required.

`allow_tracked` exempts intentional runtime-artifact fixtures from the artifact
check, but does not bypass directory coverage. `exclude_paths` only affects
reclaim discovery/sizing. Patterns match case-sensitively against POSIX paths
relative to the repository root (`*` can match `/`). Automatically detected host
paths, network observations, and script bodies are not saved in the profile;
explicit tool paths remain as supplied. A malformed saved profile fails visibly rather
than silently switching to automatic settings.

An explicit boundary manifest overrides the saved profile:

```sh
nk-cli boundaries --manifest examples/repository-units.example.json --json
nk-cli boundaries --allow 'fixtures/node_modules/*'
```

## Local service checks

```sh
nk-cli doctor --port 3000 --port 8000
nk-cli doctor --host ::1 --port 8000 --json
nk-cli doctor --manifest examples/services.example.json
```

With no target arguments, `doctor` uses saved services or explicit numeric
`--port`, `-p`, or `PORT=` hints in package `dev`/`start` scripts. Framework default
ports are not guessed. Missing service settings produce an actionable error.
Only `127.0.0.1`, `::1`, and `localhost` are accepted; `localhost` is normalized
to `127.0.0.1` without DNS. A TCP connection proves that a port accepts
connections, not that the application is healthy. Optional offline services
remain visible but do not fail the command.

`doctor --json` reports `services` without operator role/recovery metadata.
`portal-doctor` remains a compatibility alias with its old `hosts` JSON shape.
Existing host manifests remain accepted as compatibility inputs.

## Cache inventory

```sh
nk-cli reclaim --depth 4 --json
nk-cli reclaim --include build --include dist --exclude fixtures
nk-cli reclaim --root /path/to/directory --max-files 10000
```

The default is the Git root, or the current directory outside Git. An explicit
`--root` is respected. Known dependency/cache names include `node_modules`,
`__pycache__`, `.next`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.turbo`,
and `.parcel-cache`. `build`, `dist`, and `.cache` require explicit inclusion;
directory names alone cannot establish that their contents are disposable.
Saved `cache_names` add repository-specific candidates.

Depth limits candidate discovery: depth 1 includes immediate children. Sizing
then examines regular-file metadata within each candidate, up to `--max-files`.
An explicitly selected scan root that is itself a candidate is measured too.
Symlinks, Windows junctions/other reparse points inside the scan, Git internals,
and protected credential directories are skipped. This also skips cloud-file
placeholders rather than causing a download. Size caps, unreadable entries, and omitted subtrees yield
`size_complete: false`, also labeled in human output. Sizes are logical bytes,
not guaranteed reclaimable disk blocks. No delete/apply command exists.

## Install from a checkout

Linux/macOS (with Python 3.11+ available as `python3`):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/nk-cli --help
```

Windows PowerShell (with Python 3.11+ available as `python`):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\nk-cli.exe --help
```

These commands do not require virtual-environment activation or changing
PowerShell execution policy. Activate the environment or add its executable
directory to your PATH if you want to use the shorter `nk-cli` command elsewhere.
Git must also be on PATH; Tailscale and Ollama are optional discovery tools.

For development, install with `python -m pip install -e .` and run
`python -m unittest discover -s tests -v`. The optional `[dev]` extra adds pytest
for contributors who prefer it. No package-index publication is implied by these
source-install instructions.

All commands accept `--json`. Exit 0 means success, 1 means a check failed (or a
selected tool is missing from `tools`), and
2 means the command could not run because of arguments, unreadable configuration,
or another operational error. Boundary rule violations are check failures.
Discovery can succeed with warnings; inspect them before using its candidates.

See [security scope](SECURITY.md) and [public readiness](docs/PRE_PUBLISH_AUDIT.md).
MIT — see [LICENSE](LICENSE).
