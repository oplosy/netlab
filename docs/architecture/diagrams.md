# Architecture Diagrams

## Site Layer 2 and gateway behavior

```mermaid
flowchart TB
    d1[DIST-1\nRSTP root primary\nVRRP active]
    d2[DIST-2\nRSTP root secondary\nVRRP standby]
    a1[ACCESS-1]
    clients[Users / Servers / Guests]

    d1 == Port-channel 11\n2 x links ==> a1
    d2 == Port-channel 12\n2 x links ==> a1
    d1 == Port-channel 100\npeer trunk ==> d2
    a1 --- clients
```

Each port channel terminates on one peer. RSTP sees each port channel as one
logical link and blocks the redundant loop. VRRP state is aligned with RSTP root
preference to avoid avoidable east-west forwarding.

## Routing boundaries

```mermaid
flowchart LR
    hlan[HQ VLAN routes\n10.10.0.0/16]
    habr[HQ Edge\nOSPF ABR]
    babr[BR1 Edge\nOSPF ABR]
    blan[BR1 VLAN routes\n10.20.0.0/16]
    isp[ISP-1\nAS 65000]

    hlan -- OSPF Area 10 --> habr
    habr == OSPF Area 0\nover IPsec ==> babr
    babr -- OSPF Area 20 --> blan
    habr -- eBGP\ndefault-only import --> isp
    babr -- eBGP\ndefault-only import --> isp
```

Site `/16` summaries cross Area 0. BGP routes are not generally redistributed
into OSPF; the edge conditionally originates a default while an Internet
default is present.

## Observability correlation

```mermaid
flowchart LR
    test[Test traffic + run ID]
    net[Network path]
    routes[Route and protocol state]
    pcap[Packet capture]
    snmp[SNMPv3 metrics]
    flow[IPFIX records]
    logs[Syslog events]
    report[Evidence report]

    test --> net
    net --> routes
    net --> pcap
    net --> snmp
    net --> flow
    net --> logs
    routes --> report
    pcap --> report
    snmp --> report
    flow --> report
    logs --> report
```

The run ID and timestamps are the correlation keys. A dashboard screenshot
alone is not proof; it must be tied to route state, packets, and the traffic
generator for the same run.
