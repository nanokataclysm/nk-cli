# Pre-publish audit — nk-cli v0.1

**Date:** 2026-07-18  
**Auditor seat:** Mira (Grok)  
**Tree:** `~/dev/nk-cli` (isolated from monorepo)  
**Verdict:** **Ready for private operator review before first public repository create.**  
**Not done until you OK:** create GitHub repo, push, PyPI publish.

---

## 1. Package identity (chosen)

| Field | Choice | Rationale |
|-------|--------|-----------|
| **Public name** | **`nk-cli`** | Distinct from private host CLI `nk` / `nanokat`; short; PyPI-friendly |
| **Distribution name** | `nk-cli` (`pyproject.toml` project name) | Matches directory + console script |
| **Import package** | `nk_cli` | PEP 8 |
| **License** | **MIT** | Simple permissive OSS; SPDX `MIT` in `LICENSE` |
| **Version** | `0.1.0` | Alpha utilities only |

---

## 2. Candidate inventory & dependency / caller chains

### Runtime dependency graph (complete)

```text
nk-cli (console_scripts)
  └─ nk_cli.cli:main
       ├─ nk_cli.boundaries.run_boundaries
       │    ├─ json (stdlib)
       │    ├─ subprocess → git ls-files only
       │    └─ pathlib
       ├─ nk_cli.portal_doctor.{load_manifest,inspect}
       │    ├─ json, re, socket (stdlib)
       │    └─ pathlib
       └─ nk_cli.reclaim.{scan,format_human,as_jsonable}
            ├─ os, pathlib, dataclasses (stdlib)
            └─ (no subprocess, no network)
```

| Module | External deps | Subprocess / network | Callers |
|--------|---------------|----------------------|---------|
| `cli.py` | **none** (stdlib + nk_cli) | none | `__main__`, entrypoint |
| `boundaries.py` | **none** | `git ls-files` only | `cli boundaries` |
| `portal_doctor.py` | **none** | **none** in v0.1 public (localhost `socket` only) | `cli portal-doctor` |
| `reclaim.py` | **none** | none | `cli reclaim` |

**Declared `project.dependencies`:** empty.  
**Dev optional:** `pytest` (not required for `unittest` CI).  
**Plugins:** none. No setuptools entry points beyond `nk-cli`.  
**Scripts:** no shell helpers in this package.

### Monorepo provenance (source, not linked)

| Public module | Derived from (private monorepo) | Extraction notes |
|---------------|----------------------------------|------------------|
| `boundaries` | `nkscripts/check-repository-boundaries.py` | Schema versions dual-accept; generic `.chroma` rule |
| `portal_doctor` | `nkscripts/portal-doctor.py` | **Stripped** Tailscale + SSH + remote units for public v1 |
| `reclaim` | patterns from disk-reclaim skill / reclaim_ops dry path | Report-only; blocks secret-ish path segments |

No import of monorepo packages. No shared install.

---

## 3. Per-command security review

### `boundaries`

| Check | Result |
|-------|--------|
| Secrets | Does not read env secrets; prints path strings from git only |
| Host paths | Operator-supplied `--repo` / `--manifest` |
| Destructive | Read-only validation; exit code 1 on errors |
| Licensing | Original monorepo tool had no separate license; re-licensed under package MIT |

### `portal-doctor`

| Check | Result |
|-------|--------|
| Secrets | Rejects forbidden JSON keys (`token`, `password`, …) |
| Host paths | Manifest path only; listeners **must** be `127.0.0.1` |
| Network | Local TCP connect attempt only (no HTTP client) |
| Destructive | Read-only |
| Tailscale/SSH | **Rejected** in public v1 (manifest fields forbidden) |

### `reclaim`

| Check | Result |
|-------|--------|
| Secrets | Blocks scans under `.nanokat-secrets`, `.ssh`, `.gnupg`, `.aws`, `.config/gcloud` |
| Destructive | **No delete API** — dry-run report only |
| Host paths | Operator `--root` |

---

## 4. Explicitly blocked from public v1

These remain **private monorepo / operator** surfaces and must not be added without a separate redesign:

- USB / LUKS / `nanokat-priv`
- Secrets vault / rotate / crystal_castle
- Ship / promote / Vercel / CF DNS set
- Alley metal / PTY terminal bridge
- Kai / Ollama bridge / Tailscale mesh control
- Cloud backup / OCI recovery
- Production-control commands

---

## 5. Tests & CI

| Item | Status |
|------|--------|
| Unit tests | `tests/` — boundaries, portal-doctor, reclaim |
| Local run | `python -m unittest discover -s tests -v` |
| CI | `.github/workflows/ci.yml` — Python 3.11–3.13, tests, CLI smoke, stdlib-only + forbidden-string guards |

---

## 6. Final security review checklist (gate)

- [x] Isolated tree (`~/dev/nk-cli`) with own tests  
- [x] MIT LICENSE present  
- [x] No third-party runtime deps  
- [x] No Tailscale/SSH remote control in public v1 portal-doctor  
- [x] No ship/secrets/LUKS/Alley/Kai code  
- [x] CI workflow present  
- [ ] **Operator:** create empty public GitHub repo (or confirm org name)  
- [ ] **Operator:** SSH-signed tag + Sigstore/GitHub attestation on first release  
- [ ] **Peer (Sylvia):** optional second-pass security review before `git push --tags`  
- [ ] **Operator:** first publish decision (GitHub only vs GitHub + PyPI Trusted Publishing)  

---

## 7. Recommended first publish steps (when approved)

```bash
cd ~/dev/nk-cli
# after empty repo exists:
# git remote add origin git@github.com:nanokataclysm/nk-cli.git
# git push -u origin master
# git tag -s v0.1.0 -m "nk-cli 0.1.0 public assistive utilities"
# git push origin v0.1.0
```

Do **not** force-push monorepo history into this repo.  
Do **not** copy monorepo secrets or `.env*` into CI.
