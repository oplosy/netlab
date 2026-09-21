# ADR 0005: Use Multi-Area OSPF with Site Summaries

- Status: Accepted
- Date: 2026-09-21

## Context

A single area would work at this scale, but it would not demonstrate deliberate
failure-domain and summarization design. Excessive areas would add ceremony
without operational value.

## Decision

Use OSPFv2 with:

- Area 0 on encrypted inter-site links
- Area 10 at headquarters
- Area 20 at branch 1
- Area 30 reserved for branch 2
- site edges acting as ABRs
- site `/16` summaries advertised toward Area 0
- normal site areas so specific remote-site summaries remain visible
- a default conditionally originated while a valid Internet default exists
- passive interfaces by default
- BFD on routed internal adjacencies used by convergence tests

Do not redistribute the BGP table into OSPF. Only the intentional default is
injected into site areas.

## Consequences

- Each site has a bounded link-state domain.
- The backbone sees stable site summaries rather than every VLAN prefix.
- A site distribution node uses specific summaries for remote sites and the
  edge-originated default for Internet destinations.
- Summary and black-hole behavior must be tested during partial site failure.

## Reference

- <https://docs.frrouting.org/en/latest/ospfd.html>
