# OBS-180 live acceptance

Run from the phase-1 integration checkout after `make lab-up`, the SVC-160
services and gateway configuration, and `make observability-up`:

```sh
make test-observability
```

The target runs the static inventory/configuration checks and then the live
acceptance runner. The live runner verifies all nine SNMPv3 authPriv targets,
rejects a valid SNMPv2c `public` request, checks interface metrics with
`ifName` labels, leases a temporary HQ user VLAN address, sends DNS queries,
and checks the resulting IPFIX endpoint and interface indexes against SNMP.
It sends an RFC5424 test event and confirms that Loki stores it.

The link correlation check briefly brings `hq-access-1` port `eth5` down. It
waits for `ifOperStatus=down`, writes a test syslog event timestamped from that
metric sample, checks that Loki has the matching event, then brings `eth5` up
in a `finally` path and waits for `ifOperStatus=up`.

The test needs the Docker CLI connected to the already-running lab, the
`netlab/service` image pinned by `versions.env`, the observability stack, and
the Prometheus/Loki endpoints on OOB `172.31.255.14`. It does not modify Docker
Desktop or Engine settings. DHCP leases and link state are cleaned up by the
test helper.
