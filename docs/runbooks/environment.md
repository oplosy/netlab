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
| uv | `0.12.0` |
| CPU | 4 vCPU minimum |
| Memory | 8 GiB minimum |
| Free disk | 40 GiB on repository and Docker filesystems |

Container image references are locked by IMG-020 before any image build or lab
deployment. Image tags must resolve to immutable digests; `latest`, `stable`,
and other floating tags are prohibited.

The ADR-0011 Python toolchain is declared in `pyproject.toml` for Python
3.12 and resolved in `uv.lock`. The repository policy requires `uv 0.12.0`
exactly, even though `pyproject.toml` expresses the compatible range as
`>=0.12,<0.13`. Use `uv run --locked` for Python tooling. The environment
preflight has no Python dependency and never installs packages, but it does
read `uv --version` and fails when the exact pinned release is unavailable.

### Install the pinned uv release

Run this inside the dedicated WSL2 distribution, not in Windows PowerShell:

```sh
curl -LsSf https://astral.sh/uv/0.12.0/install.sh | sh
exec "$SHELL" -l
uv --version
```

The last command must report `uv 0.12.0`. This is the only installation step;
`make preflight` remains read-only and will not repair a missing or mismatched
installation.

### Shared Windows/WSL checkouts

Do not reuse a Windows-created `.venv` from the Linux WSL environment. Python
virtual environments contain platform-specific executables. Keep the WSL
environment separate by setting the project environment before using `uv`:

```sh
export UV_PROJECT_ENVIRONMENT="${HOME}/.cache/netlab/.venv-wsl"
uv sync --locked
uv run --locked pytest
```

The path is deliberately outside the checkout, so a Windows `.venv` cannot be
selected accidentally and the Linux environment does not create an untracked
repository directory.

## Linux bonding module for L2 switching

The site switching topology uses Linux 802.3ad bonds beneath OVS RSTP. Load
the WSL kernel module once before make lab-up:

    wsl.exe -d Ubuntu -u root -- modprobe bonding

Confirm it is available inside WSL:

    test -d /sys/module/bonding

The read-only preflight does not load kernel modules.

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

The uv failure is intentionally explicit:

```text
[FAIL] uv command: uv is not installed or not on PATH
       action: install the pinned uv release inside WSL2; see docs/runbooks/environment.md
```

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

## Keep one WSL session for runtime acceptance

Run the complete multi-command lifecycle from one persistent WSL shell. Do not
run `make lab-up`, inspection, and teardown as separate short-lived
`wsl.exe ... -- bash -lc '...'` calls. When the WSL process exits, systemd and
Docker can be stopped or restarted; a restored Containerlab runtime may then
lose data links such as `eth1` even though containers still exist.

Recommended PowerShell/WSL usage:

```powershell
wsl.exe -d Ubuntu -u atlas
```

Keep that shell open and run inside it:

```sh
cd /mnt/c/Users/<user>/Desktop/workspace/A-projects/netlab
make preflight
make topology
make lab-up
make smoke
make lab-down
exit
```

If automation must use one-shot PowerShell commands, keep the entire
acceptance sequence in one WSL process and do not exit until teardown has
finished:

```powershell
wsl.exe -d Ubuntu -u atlas -- bash -lc 'cd /mnt/c/Users/<user>/Desktop/workspace/A-projects/netlab && make lab-up && make smoke && make lab-down'
```

## Custom WSL kernel for XFRM interfaces

The route-based IPsec design requires `CONFIG_XFRM_INTERFACE=y`. The preflight
only reads kernel configuration and module metadata; it never loads modules or
installs a kernel. Use the stock WSL2 kernel when it passes the feature check.

If a custom kernel is required, build it from the Microsoft WSL2 kernel source
and keep the resulting `bzImage`, configuration, and module VHDX as host-local
artifacts. They must not be committed to this repository. Configure
`%UserProfile%\.wslconfig` with paths appropriate to the host:

```ini
[wsl2]
kernel=C:\Users\<user>\.wsl-kernels\netlab\bzImage-<release>
kernelModules=C:\tmp\netlab-wsl-modules-<release>.vhdx
```

On WSL 2.7.3, placing `kernelModules` under `C:\Users\<user>` can fail
with `E_ACCESSDENIED`; use a path outside the profile, such as `C:\tmp`, and
verify that the VHDX is readable by WSL. After changing `.wslconfig`, restart
WSL and verify the running kernel:

```powershell
wsl.exe --shutdown
wsl.exe -d Ubuntu -- uname -r
```

The release string must match the custom image and its module metadata. Keep
the host-local artifact inventory in the operator's machine notes; this
repository documents only the requirement and verification procedure.

## Updating the lock

Version changes are implementation-impacting. Update `versions.env`, update
this runbook, and rerun `make preflight` on the intended supported host in the
same reviewed change. Record resolved immutable image digests in `versions.env`
when IMG-020 adds images. Never use an unqualified version or floating image
tag.
