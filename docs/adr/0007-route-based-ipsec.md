# ADR 0007: Use Certificate-Based Route-Based IPsec

- Status: Accepted
- Date: 2026-09-21

## Decision

Use strongSwan's modern `swanctl`/VICI interface, IKEv2, certificate
authentication, and Linux XFRM interfaces. OSPF Area 0 runs over the XFRM link.
The baseline cryptographic profile is AES-256-GCM with ECDH P-384.

Generate a lab CA and node certificates during bootstrap. Store neither private
keys nor generated certificates in Git. Rekeying and tunnel restoration are
part of verification.

## Consequences

- Routing does not depend on large policy-based traffic-selector lists.
- The underlay can be captured independently from decrypted overlay traffic.
- PKI bootstrap and time synchronization become hard dependencies.
- MTU/MSS behavior must be measured and configured rather than assumed.

## Reference

- <https://docs.strongswan.org/docs/latest/index.html>
