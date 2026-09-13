# Public readiness review

Updated 2026-09-13. This replaces the original extraction inventory with the
current public interface. No publishing or deployment is part of this change.

## Product scope

Six commands serve an ordinary developer checkout:

1. `analyze`: identify project metadata and suggest editable repository settings.
2. `tools`: discover installed agent/runtime executables and explicit model preferences.
3. `targets`: inspect Git, SSH, LAN, WAN, and Tailscale endpoint candidates.
4. `boundaries`: check tracked artifacts, with optional explicit directory policy.
5. `doctor`: test explicitly configured local TCP listeners.
6. `reclaim`: inventory selected cache directories without deleting them.

`portal-doctor` and existing manifest versions remain compatibility inputs.
Runtime discovery uses local tools and explicit preferences. Modern service
reports omit operator role/recovery data; legacy compatibility inputs remain supported.
No private host inventory or required AI provider is needed. Adaptation uses
repository metadata, installed executables, and editable `.nk-cli.json` preferences.
Detected network/model inventories stay out of the shared profile.

## Verified behavior

The local unittest suite covers mixed Node/Python/Rust/Go repositories,
CMake/Make suggestions, nested paths and linked worktrees, inherited package
managers, ambiguous lockfiles, malformed metadata, profile preservation,
profile use by all three checks, local-only probes, and reclaim exclusions,
symlinks, depth boundaries, and partial sizes.

Boundary enforcement now checks exact nested path prefixes and accepts simple
unit names. Artifact exemptions allow deliberate fixtures. Reclaim no longer
treats arbitrary build/dist directories as default candidates, follows symlink
targets while sizing, or presents capped sizes as complete. Optional offline
services no longer fail required service checks.

The implementation remains Python standard-library-only. The CI definition now
covers Python 3.11/3.13 on Linux, macOS, and Windows, plus Python 3.14 on Linux.
Local Linux execution is distinct from the scheduled CI matrix; unexecuted
platform jobs are not evidence of compatibility.

The discovery pass adds checks for repository PATH shims, relative repository
paths, metadata time/output limits, explicit unavailable tool preferences,
local-only model inventory, URL credential redaction, IPv6 parsing, Git push-URL
precedence, passive neighbor/peer records, and bounded TCP probes. Obsolete
owner-marker string guards were removed in favor of these behavioral tests.

On 2026-09-13, all 86 local unit tests passed. A wheel built from an isolated
source copy was installed without dependencies into a fresh virtual environment.
The installed CLI passed an acceptance run in a separate Git fixture whose path
contained spaces and an apostrophe. Relative repository selection, custom tool
and model preferences, profile reuse, boundary checks, Git push-URL precedence,
credential redaction, and a real loopback target probe passed. Profile overwrite
was refused, all fixture source files retained their original hashes, and no
agent or project scripts executed.

Read-only Linux discovery also returned candidates from installed tools, the
local Ollama catalog, Git remotes, cached LAN neighbors, and local Tailscale
status. No remote target was probed, no inference ran, and no data was pushed.
This proves the packaged local discovery flow; it does not establish build
success, remote authentication, or push permission. The Tailscale CLI contract
was checked against its [official reference](https://tailscale.com/docs/reference/tailscale-cli),
which warns that the JSON format can change.

## Remaining limits

- Analysis and boundary checks require a Git working tree. Reclaim and explicit
  local port checks, tool inventory, and explicit/passive target discovery also
  work outside Git.
- Detection covers recognized metadata and literal task/port declarations, not
  every build system, shell expression, workspace resolver, or framework default.
- Generated directory rules are a top-level snapshot. Maintainers should narrow
  them when architectural ownership requires finer boundaries.
- Discovered tasks are suggestions only. There is no automatic dependency
  installation or task execution, and analysis does not prove build/test success.
- Tool-name heuristics do not establish agent capabilities. Explicit models are
  unverified; automatic model listing currently supports local Ollama only.
- Git target discovery reads literal local remote settings, not included/global
  rewrites or SSH Host/ProxyJump configuration. WAN candidates must already be
  configured or explicitly supplied. macOS/Windows neighbor discovery uses
  cached IPv4 ARP only; Tailscale JSON can change between versions.
- An online peer or successful TCP connection does not establish push access.
  There is no push, file transfer, agent invocation, login, or deployment command.
- Cache totals are estimates and never authorize deletion.
- Package publication, remote CI results, and acceptance on another person's
  workstation remain separate release gates.

The [README](../README.md) documents setup, settings, and migration-compatible
inputs. [SECURITY.md](../SECURITY.md) records the execution and filesystem scope.
