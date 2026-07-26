# nk-cli

Public-safe command-line utilities for repository hygiene, local diagnostics, and dry-run maintenance workflows.

`nk-cli` is deliberately narrow. It does not expose the private NANOKAT control plane, deployment operations, secret stores, recovery tooling, remote shells, or privileged device management.

## Capabilities

| Command | Purpose |
|---|---|
| `nk-cli boundaries` | Validate repository-unit boundaries and reject tracked runtime junk |
| `nk-cli portal-doctor` | Read-only local service health checks from a sanitized manifest |
| `nk-cli reclaim` | Produce a dry-run disk-reclaim report without deleting files |

## Install for development

```bash
git clone https://github.com/nanokataclysm/nk-cli.git
cd nk-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
nk-cli --help
```

## Examples

```bash
# Validate repository boundaries
nk-cli boundaries \
  --repo /path/to/repo \
  --manifest examples/repository-units.example.json

# Check local service health using safe manifest fields
nk-cli portal-doctor \
  --manifest examples/portal-hosts.example.json

# Report reclaimable cache space without deleting anything
nk-cli reclaim --root "$HOME" --json
```

## Safety model

- No production deployment, DNS, or cloud-promotion commands
- No secret-vault or credential-store defaults
- No disk wiping, encrypted-volume management, or privileged USB operations
- No remote shell brokers, PTY relays, or mesh-network control
- Localhost-only portal checks with secret, IP, SSH, and mesh fields rejected
- Destructive maintenance is excluded; reclaim remains report-only

See [`docs/PRE_PUBLISH_AUDIT.md`](docs/PRE_PUBLISH_AUDIT.md) for the public-surface review.

## Project status

This repository is an early public extraction of reusable NANOKAT operational patterns. The current focus is keeping the interface small, auditable, testable, and safe to run on a developer workstation.

Issues and focused pull requests are welcome. Please avoid submitting host-specific paths, credentials, network inventories, or private infrastructure details.

## License

MIT — see [`LICENSE`](LICENSE).
