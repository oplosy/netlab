# SEC-170 Live Security Evidence

- Run time (UTC): 2026-09-24T22:19:24.733644+00:00
- Runtime: existing project WSL2 lab; Docker Desktop and daemon settings were not changed.
- Result: PASS

## Results

### hq

- guest_dns: 203.0.113.20
- guest_dns_nat_packets_before_after: 1->2
- isp_dns_allow_packets_before_after: 9->10
- guest_dns_tcp: 203.0.113.20
- guest_dns_tcp_nat_packets_before_after: 1->2
- isp_dns_tcp_allow_packets_before_after: 1->2
- guest_ntp: NTP server mode=4 bytes=48
- snat_public_endpoint: 203.0.113.129
- isp_service_allow_packets: 80
- guest_nat_rule_packets_before_after: 6->7
- denied_flows: server,inband,corporate,oob
- per_flow_forward_deny_packets_before_after: {"corporate": "forward:29->31", "inband": "input:20->22", "oob": "forward:31->33", "server": "forward:27->29"}
- unauthorized_bfd_input_packets_before_after: 22->23

### br1

- guest_dns: 203.0.113.20
- guest_dns_nat_packets_before_after: 1->2
- isp_dns_allow_packets_before_after: 3->4
- guest_dns_tcp: 203.0.113.20
- guest_dns_tcp_nat_packets_before_after: 1->2
- isp_dns_tcp_allow_packets_before_after: 1->2
- guest_ntp: NTP server mode=4 bytes=48
- snat_public_endpoint: 203.0.113.130
- isp_service_allow_packets: 99
- guest_nat_rule_packets_before_after: 8->9
- denied_flows: server,inband,corporate,oob
- per_flow_forward_deny_packets_before_after: {"corporate": "forward:26->28", "inband": "input:18->20", "oob": "forward:28->30", "server": "forward:24->26"}
- unauthorized_bfd_input_packets_before_after: 20->21

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

- correlated_deny_log_events_observed: 19

### Control-plane negative test

- oob_aaa_and_break_glass: PASS

## Deny log sample

```text
[Fri Sep 25 01:19:10 2026] SEC170|br1-dist-1|forward|deny IN=vlan30 OUT=eth0 MAC=06:4d:d0:46:ac:ea:aa:c1:ab:bd:23:a3:08:00 SRC=10.20.30.100 DST=172.31.255.50 LEN=60 TOS=0x00 PREC=0x00 TTL=63 ID=17017 DF PROTO=TCP SPT=51768 DPT=22 WINDOW=56760 RES=0x00 SYN URGP=0 
[Fri Sep 25 01:19:11 2026] SEC170|br1-dist-1|forward|deny IN=vlan30 OUT=eth0 MAC=06:4d:d0:46:ac:ea:aa:c1:ab:bd:23:a3:08:00 SRC=10.20.30.100 DST=172.31.255.50 LEN=60 TOS=0x00 PREC=0x00 TTL=63 ID=17018 DF PROTO=TCP SPT=51768 DPT=22 WINDOW=56760 RES=0x00 SYN URGP=0 
[Fri Sep 25 01:19:13 2026] SEC170|br1-dist-1|input|deny IN=vlan20 OUT= MAC=ee:e6:55:90:92:05:aa:c1:ab:a5:2f:13:08:00 SRC=10.20.20.100 DST=10.20.20.2 LEN=34 TOS=0x00 PREC=0x00 TTL=64 ID=26382 DF PROTO=UDP SPT=33023 DPT=3784 LEN=14 
```
