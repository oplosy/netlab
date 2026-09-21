# ADR 0008: Separate Management and Enforce Default-Deny Zones

- Status: Accepted
- Date: 2026-09-21

## Decision

Use the Containerlab management network as the primary OOB plane. Disable its
external masquerading, assign fixed addresses, and exclude it from OSPF and BGP.
Administrative services listen on OOB addresses.

Use five security zones: users, servers, guest, in-band management, and OOB
management. nftables statefully permits documented flows and denies everything
else. Guest policy permits DHCP, approved DNS/NTP, and NAT to simulated Internet
services; it denies all enterprise and management destinations.

Use FreeRADIUS for central AAA with a generated local break-glass account.
Use SNMPv3 `authPriv`; SNMPv2c is prohibited.

## Consequences

- The lab remains manageable during data-plane failures.
- OOB compromise is outside the simulated enterprise forwarding path and must
  be treated as host compromise.
- Security completion requires negative tests and denied-flow logs.
