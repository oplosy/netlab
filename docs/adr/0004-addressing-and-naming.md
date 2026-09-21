# ADR 0004: Use Hierarchical Site Addressing and Deterministic Names

- Status: Accepted
- Date: 2026-09-21

## Decision

Allocate one `/16` per site, repeat VLAN IDs and subnet roles, reserve `/31`
point-to-point pools and `/32` loopback pools, and use documentation prefixes
for the simulated ISP. Follow the allocations in
`docs/architecture/addressing.md`.

Names use `<site>-<role>-<index>`. IP addresses are declared once in the
authoritative inventory and rendered into topology, configuration, and
documentation. Hard-coded duplicate address definitions fail validation.

## Consequences

- Site routes can be summarized without hiding another site's prefixes.
- A second branch can reuse templates without extending a Layer 2 domain.
- Some address space is intentionally reserved to keep future changes stable.
