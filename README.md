# nk-cli

Small **public-safe** assistive utilities inspired by NANOKAT host practices.

This is **not** the private host-control CLI (`nk` / `nanokat` in the monorepo).  
It deliberately **omits** ship, secrets vaults, USB/LUKS recovery, Alley metal, Kai bridges, and PTY relays.

## Install (local)

```bash
git clone https://github.com/nanokataclysm/nk-cli.git
cd nk-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
nk-cli --help
```

## Commands

| Command | Purpose |
|---------|---------|
| `nk-cli boundaries` | Validate a git monorepo unit manifest; reject tracked runtime junk |
| `nk-cli portal-doctor` | Read-only host/portal health from a local manifest (no logins, no repairs) |
| `nk-cli reclaim` | **Dry-run** disk reclaim report for common cache paths (never deletes) |

### Examples

```bash
# Repository unit boundaries
nk-cli boundaries --repo /path/to/repo --manifest examples/repository-units.example.json

# Portal doctor (safe fields only)
nk-cli portal-doctor --manifest examples/portal-hosts.example.json

# Disk reclaim report only
nk-cli reclaim --root "$HOME" --json
```

## Security posture

- **No** default paths under secret vaults or credential stores  
- **No** production deploy / DNS / cloud promote verbs  
- **No** LUKS, wipe, or privileged USB tooling  
- **No** remote shell brokers / PTY relays / Tailscale control  
- Portal doctor (public v1): localhost listeners only; rejects secrets, IPs, mesh/SSH fields  
- Full pre-publish audit: [docs/PRE_PUBLISH_AUDIT.md](docs/PRE_PUBLISH_AUDIT.md)

## Provenance

Extracted from NANOKAT monorepo patterns after inventory job `c40b1b98ac5c` (2026-07-18).  
Private control-plane remains in the monorepo host CLI (`nk` / `nanokat`).

## License

MIT — see [LICENSE](LICENSE).

## Signature / release (planned)

- **Git:** SSH-signed annotated tags  
- **Artifacts:** GitHub/Sigstore attestations on release  
- Not monorepo receipt keys / vault material
