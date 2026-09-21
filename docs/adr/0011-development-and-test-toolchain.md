# ADR 0011: Standardize the Development and Test Toolchain

- Status: Accepted
- Date: 2026-09-21

## Decision

Use a Linux-first toolchain executed inside the dedicated WSL2 environment:

- GNU Make as the documented human and CI command surface
- Python in a repository virtual environment for renderers and test tooling
- `uv` with a lock file for Python dependency resolution
- Ansible Core plus pinned collections for configuration orchestration
- Jinja2 templates fed only by validated inventory data
- JSON Schema for declarative inventory validation
- pytest for unit, integration, security, and evidence assertions
- Ruff, mypy, yamllint, ansible-lint, ShellCheck, and markdownlint for static checks
- gitleaks for committed-secret detection
- GitHub Actions for static and unit checks
- a local or self-hosted Linux runner for privileged Containerlab smoke and
  end-to-end tests

Pin major/minor tool versions and all Python dependencies. Resolve container
images to immutable digests. Do not use a GitHub-hosted runner as proof that the
full privileged lab works unless the complete topology actually ran there.

## Consequences

- Contributors use a small set of stable commands such as `make lint`,
  `make test-unit`, and `make evidence-phase-1`.
- Full network tests remain separate from fast pull-request checks.
- Lock files and generated configuration must be reviewed like source code.
- PowerShell may invoke WSL commands but is not the implementation shell for lab
  lifecycle scripts.
