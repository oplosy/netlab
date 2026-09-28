# SEC-170 Live Security Evidence

- Run time (UTC): 2026-09-28T07:53:52.079752+00:00
- Runtime: existing project WSL2 lab; Docker Desktop and daemon settings were not changed.
- Result: PASS

## Results

### hq

- guest_dns: 203.0.113.20
- guest_dns_nat_packets_before_after: 0->1
- isp_dns_allow_packets_before_after: 0->1
- guest_dns_tcp: 203.0.113.20
- guest_dns_tcp_nat_packets_before_after: 0->1
- isp_dns_tcp_allow_packets_before_after: 0->1
- guest_ntp: NTP server mode=4 bytes=48
- snat_public_endpoint: 203.0.113.129
- isp_service_allow_packets: 17
- guest_nat_rule_packets_before_after: 0->1
- denied_flows: server,inband,corporate,oob
- per_flow_forward_deny_packets_before_after: {"corporate": "forward:2->4", "inband": "input:12->14", "oob": "input:14->16", "server": "forward:0->2"}
- unauthorized_bfd_input_packets_before_after: 16->17

### br1

- guest_dns: 203.0.113.20
- guest_dns_nat_packets_before_after: 0->1
- isp_dns_allow_packets_before_after: 0->1
- guest_dns_tcp: 203.0.113.20
- guest_dns_tcp_nat_packets_before_after: 0->1
- isp_dns_tcp_allow_packets_before_after: 0->1
- guest_ntp: NTP server mode=4 bytes=48
- snat_public_endpoint: 203.0.113.130
- isp_service_allow_packets: 17
- guest_nat_rule_packets_before_after: 0->1
- denied_flows: server,inband,corporate,oob
- per_flow_forward_deny_packets_before_after: {"corporate": "forward:2->4", "inband": "input:12->14", "oob": "input:14->16", "server": "forward:0->2"}
- unauthorized_bfd_input_packets_before_after: 16->17

### br2

- guest_dns: 203.0.113.20
- guest_dns_nat_packets_before_after: 0->1
- isp_dns_allow_packets_before_after: 0->1
- guest_dns_tcp: 203.0.113.20
- guest_dns_tcp_nat_packets_before_after: 0->1
- isp_dns_tcp_allow_packets_before_after: 0->1
- guest_ntp: NTP server mode=4 bytes=48
- snat_public_endpoint: 203.0.113.133
- isp_service_allow_packets: 13
- guest_nat_rule_packets_before_after: 0->1
- denied_flows: server,inband,corporate,oob
- per_flow_forward_deny_packets_before_after: {"corporate": "forward:2->4", "inband": "input:12->14", "oob": "input:14->16", "server": "forward:0->2"}
- unauthorized_bfd_input_packets_before_after: 16->17

### Control-plane negative test

- source_site: hq
- source_ip: 10.10.10.100
- destination_site: br1
- destination_ip: 10.20.10.100
- result: ICMP echo reply received

### Control-plane negative test

- source_site: br1
- source_ip: 10.20.10.100
- destination_site: hq
- destination_ip: 10.10.10.100
- result: ICMP echo reply received

### Control-plane negative test

- source_site: hq
- source_ip: 10.10.10.100
- destination_site: br2
- destination_ip: 10.30.10.100
- result: ICMP echo reply received

### Control-plane negative test

- source_site: br2
- source_ip: 10.30.10.100
- destination_site: hq
- destination_ip: 10.10.10.100
- result: ICMP echo reply received

### Control-plane negative test

- correlated_deny_log_events_observed: 0
- kernel_log_visibility: unavailable; per-rule nft counters and failed probes are authoritative

### Control-plane negative test

- oob_aaa_and_break_glass: PASS

## Deny log sample

```text
```
