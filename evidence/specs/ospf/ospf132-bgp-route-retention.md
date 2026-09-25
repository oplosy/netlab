# OSPF-132 BGP route retention

- Result: PASS
- Date: 2026-09-25
- FRR: 8.4.4-1.1ubuntu6.7
- Runtime: dedicated Ubuntu WSL2 with the existing Docker Engine
- Docker settings changed: no

## Procedure

1. Start from an empty Phase 1 lab and deploy with `make lab-up`.
2. Apply the IPsec overlay.
3. Apply `automation/roles/ospf/apply.sh` twice.
4. After each application, inspect `show bgp summary` and `ip -4 route show default` on both site edges.
5. Apply the gateway policy and run `make test-services`.
6. Tear down with `make lab-down` and verify with `make verify-clean`.

## Results

| Router | OSPF application | eBGP peer state | Prefixes received | Default route |
|---|---:|---|---:|---|
| HQ edge | First | Established | 1 | `via 192.0.2.1 dev eth3 proto bgp metric 20` |
| Branch 1 edge | First | Established | 1 | `via 192.0.2.3 dev eth3 proto bgp metric 20` |
| HQ edge | Second | Established | 1 | `via 192.0.2.1 dev eth3 proto bgp metric 20` |
| Branch 1 edge | Second | Established | 1 | `via 192.0.2.3 dev eth3 proto bgp metric 20` |

`make test-services` passed: 14 plan tests, repeated service convergence, DHCP/DNS/NTP acceptance at both sites, guest denial tests, central AAA acceptance, and OOB/break-glass acceptance. Final `make verify-clean` reported no `netlab-phase-1` containers or `netlab-mgmt` network.
