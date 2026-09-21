# Environment preflight

The baseline runs Containerlab and Docker Engine inside a dedicated WSL2 Linux
distribution (ADR-0001). Docker Desktop integration is not a runtime
dependency. Run repository lifecycle commands from that distribution.

## Pinned policy

`versions.env` is the reviewed environment lock for ENV-010. The preflight
requires the exact Docker Engine, Docker CLI, and Containerlab versions in that
file; it does not silently accept a newer or older version. Kernel and resource
values are minimums because WSL2 kernel patch releases and host sizing vary.

| Requirement | Policy |
|---|---|
| WSL2 kernel | `>= 5.15.0` |
| Docker Engine | `29.6.2` |
| Docker CLI | `29.6.2` |
| Containerlab | `0.77.0` |
| CPU | 4 vCPU minimum |
| Memory | 8 GiB minimum |
| Free disk | 40 GiB on repository and Docker filesystems |

Container image references are locked by IMG-020 before any image build or lab
deployment. Image tags must resolve to immutable digests; `latest`, `stable`,
and other floating tags are prohibited.

The ADR-0011 Python toolchain is declared in `pyproject.toml` for Python
3.12 and resolved in `uv.lock`. Use `uv run --locked` for Python tooling;
the environment preflight itself has no Python or uv dependency and never
installs packages.

## Run the check

From the dedicated WSL2 distribution, at the repository root:

```sh
make preflight
```

The command is read-only. `scripts/preflight/check.sh` only reads kernel,
filesystem, process, and Docker/Containerlab version information. It never
installs or removes software, loads kernel modules, creates a network
namespace, starts a container, or changes Docker state.

A supported host ends with:

```text
N checks completed: N passed, 0 failed
Preflight passed. No host software or runtime state was changed.
```

The exact check count can grow when policy adds a prerequisite. Every failed
check includes an `action:` line. A non-zero result must be resolved before
`make lab-up` or other runtime commands.

For the unloaded-module capability path, run:

```sh
make preflight-self-check
```

This only verifies the reviewed `modinfo` fallback and reads module metadata;
it does not load a module.

## Common failures

- **WSL2**: enter the dedicated distro with `wsl -d <distro>` and rerun. A
  Windows PowerShell shell is not the implementation environment.
- **Docker**: install the pinned Engine and CLI inside WSL2 and ensure the
  current user can read the Docker socket. Do not switch to Docker Desktop.
- **Version mismatch**: install the exact versions in `versions.env`, or submit
  a reviewed lock-file change.
- **Kernel feature**: use a WSL2 kernel with the reported feature enabled. The
  preflight does not load modules; restart WSL after changing the kernel.
- **Resources**: increase WSL2 CPU/memory allocation or free space on the
  filesystem reported by the check.

## Updating the lock

Version changes are implementation-impacting. Update `versions.env`, update
this runbook, and rerun `make preflight` on the intended supported host in the
same reviewed change. Record resolved immutable image digests in `versions.env`
when IMG-020 adds images. Never use an unqualified version or floating image
tag.
