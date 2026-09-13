# Security policy

nk-cli inspects repository metadata, installed tools, candidate push endpoints,
local listeners, and cache sizes. It does not install packages, invoke agents,
run discovered scripts, modify Git state, delete files, deploy, authenticate
remotely, or transfer repository contents.
The only intentional source-tree write is the user's explicit
`analyze --write-config`, which creates a new `.nk-cli.json` and refuses to
overwrite an existing file or symlink.

Repository manifests are untrusted data. Analysis reads recognized regular
metadata files, limited to 1 MiB each and 1,000 candidates. It rejects symlinked
metadata, Windows junctions/other reparse points, and protected paths. Git's filesystem monitor is disabled for inspection;
ambient Git variables cannot redirect the selected repository. Automatic command
lookup skips relative PATH entries and executables inside the selected working
tree. Git inspection uses an absolute executable and a normalized repository
path. Metadata subprocesses have time/output limits and discard stderr; Git file
listing is capped at 16 MiB/30 seconds, other discovery output at 1 MiB/3 seconds.
Task suggestions contain command argument lists, not copied script bodies.
Users must review scripts before executing any suggestion themselves.

Doctor validates targets before connecting. Only IPv4/IPv6 loopback is accepted;
`localhost` is mapped to `127.0.0.1`, without DNS. The timeout is 0.3 seconds per
listener. No HTTP requests, credentials, SSH, or remote probes are involved.
A successful connection is not an application-health or security assessment.
Modern doctor output contains service data; operator role/recovery fields remain
only in the legacy alias output.

Tool discovery does not launch discovered agents or inspect their credentials.
Windows batch launchers are inventoried but never run by metadata probes.
Explicit executable paths are inventoried without execution. Model IDs are
unverified preferences. Opt-in `--list-models` launches the installed Ollama
metadata command against fixed loopback, overriding remote endpoint/proxy
settings. Other runtime/model catalogs require explicit model IDs. No inference,
model download, or provider authentication is attempted.

Target discovery reads local Git remote settings. Opt-in LAN discovery reads
cached neighbors, and opt-in Tailscale discovery calls its local status command.
There are no subnet/WAN sweeps, SSH config/key reads, route changes, or logins.
Userinfo, URL paths/queries, and peer user metadata do not enter reports. Network
observations are never written to a repository profile. Private endpoint reports
still contain host addresses; review them before sharing.

`targets --probe` (also available on `analyze`) may resolve DNS and open TCP
connections to configured/observed remote candidates. It checks at most 16
distinct endpoints with a 0.6-second deadline each; DNS waiting is bounded, though
an OS resolver thread can finish later. At most 128 candidates are reported.
No credentials or application payloads are sent. Reachability never establishes
push permission, trusted host identity, or a valid destination directory.

Reclaim reads file metadata, not file contents. It skips Git internals, recognized
credential directories, and symlinks/Windows reparse points within the scan. An explicitly supplied
root is resolved before inspection. Reports are estimates, may race with ordinary
filesystem changes, and are not a guarantee that a directory can be deleted safely.
This CLI is not a sandbox against a hostile process changing the filesystem under
the same user account.

To report a vulnerability, use the repository's private security advisory channel
if available, or contact the maintainer through the project homepage. Do not
include live credentials or private repository contents in a public issue.
