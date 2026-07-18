# Security policy (public nk-cli)

## Scope

This repository publishes **assistive, mostly offline** utilities. It is not a general host admin tool.

## Explicitly out of scope

- Production ship / promote / DNS mutation  
- Secret vaults, key rotation, credential transport  
- USB / LUKS recovery tooling  
- Browser PTY / terminal relays  
- Alley Worker Zero control plane  
- Kai / Ollama remote bridges  

Those remain private monorepo / operator concerns and require separate security design before any public release.

## Reporting

If you find a vulnerability in this public package, open a private security advisory on the public repo once published, or contact the operator through the project homepage.
