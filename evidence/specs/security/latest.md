# SEC-170 Live Security Evidence

- Run time (UTC): 2026-09-27T20:31:07.006311+00:00
- Runtime: existing project WSL2 lab; Docker Desktop and daemon settings were not changed.
- Result: PASS

## Results

### hq

- guest_dns: 203.0.113.20
- guest_dns_nat_packets_before_after: 5->6
- isp_dns_allow_packets_before_after: 7->8
- guest_dns_tcp: 203.0.113.20
- guest_dns_tcp_nat_packets_before_after: 5->6
- isp_dns_tcp_allow_packets_before_after: 5->6
- guest_ntp: NTP server mode=4 bytes=48
- snat_public_endpoint: 203.0.113.129
- isp_service_allow_packets: 32
- guest_nat_rule_packets_before_after: 5->6
- denied_flows: server,inband,corporate,oob
- per_flow_forward_deny_packets_before_after: {"corporate": "forward:28->30", "inband": "input:42->44", "oob": "input:44->46", "server": "forward:26->28"}
- unauthorized_bfd_input_packets_before_after: 46->47

### br1

- guest_dns: 203.0.113.20
- guest_dns_nat_packets_before_after: 4->5
- isp_dns_allow_packets_before_after: 6->7
- guest_dns_tcp: 203.0.113.20
- guest_dns_tcp_nat_packets_before_after: 4->5
- isp_dns_tcp_allow_packets_before_after: 4->5
- guest_ntp: NTP server mode=4 bytes=48
- snat_public_endpoint: 203.0.113.130
- isp_service_allow_packets: 32
- guest_nat_rule_packets_before_after: 4->5
- denied_flows: server,inband,corporate,oob
- per_flow_forward_deny_packets_before_after: {"corporate": "forward:26->28", "inband": "input:35->37", "oob": "input:37->39", "server": "forward:24->26"}
- unauthorized_bfd_input_packets_before_after: 39->40

### br2

- guest_dns: 203.0.113.20
- guest_dns_nat_packets_before_after: 3->4
- isp_dns_allow_packets_before_after: 5->6
- guest_dns_tcp: 203.0.113.20
- guest_dns_tcp_nat_packets_before_after: 3->4
- isp_dns_tcp_allow_packets_before_after: 3->4
- guest_ntp: NTP server mode=4 bytes=48
- snat_public_endpoint: 203.0.113.133
- isp_service_allow_packets: 26
- guest_nat_rule_packets_before_after: 3->4
- denied_flows: server,inband,corporate,oob
- per_flow_forward_deny_packets_before_after: {"corporate": "forward:19->21", "inband": "input:33->35", "oob": "input:35->37", "server": "forward:17->19"}
- unauthorized_bfd_input_packets_before_after: 37->38

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
