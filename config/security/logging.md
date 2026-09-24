# SEC-170 deny-log runtime prerequisite

The nftables policy emits correlated, rate-limited kernel log records with the `SEC170|<node>|<chain>|deny` prefix.

Linux disables kernel logging from non-init network namespaces by default. The project containers each use their own network namespace, so a WSL operator must temporarily run this in the lab WSL distribution before `make test-security` to collect real deny records:

```sh
sudo sysctl -w net.netfilter.nf_log_all_netns=1
```

The value is kernel runtime state, not a Docker Desktop or Docker Engine setting. Restore the prior value after the live acceptance run if this WSL distribution is shared:

```sh
sudo sysctl -w net.netfilter.nf_log_all_netns=0
```

This requirement follows the Linux kernel's [`nf_log_all_netns` documentation](https://docs.kernel.org/networking/netfilter-sysctl.html), which says the default `0` restricts kernel log output to the init network namespace to prevent containers from filling host logs. SEC-170 log rules themselves limit each chain to 10 records per second with a burst of 20.
