# ADR 0001: Run Containerlab in a Dedicated WSL2 Environment

- Status: Accepted
- Date: 2026-09-21

## Context

The lab must be reproducible, version-controlled, automatable, and usable on a
Windows workstation. GNS3 and CML provide mature appliance workflows, but CML
requires licensing and both approaches make a fully open clean rebuild harder.

## Decision

Use Containerlab inside a dedicated WSL2 Linux distribution with Docker Engine
running inside the distribution. Do not make Docker Desktop integration a
runtime dependency. Declare topology in YAML and expose lifecycle operations
through repository commands.

The Containerlab management network uses a fixed subnet and disables IP
masquerading. All functional Internet tests use the simulated ISP.

## Consequences

- The project is portable as text plus pinned container images.
- Linux networking behavior is the primary target.
- The environment requires hardware virtualization and WSL2.
- Vendor-specific NOS behavior is not claimed.
- CML may later be added as an optional compatibility profile, not as the
  baseline.

## References

- <https://containerlab.dev/windows/>
- <https://containerlab.dev/manual/topo-def-file/>
- <https://containerlab.dev/manual/network/>
