# IMG-025 strongSwan OpenSSL provider

## Build and verification

Executed in the dedicated Ubuntu WSL distribution on the IMG-025 task branch:

```text
make images                 PASS
make verify-images          PASS
make image-report           PASS
```

The network-node image installs `libstrongswan-standard-plugins` at the same
locked version as `charon-systemd` and `strongswan-swanctl`:
`5.9.13-2ubuntu4.24.04.4`. The Docker build checks the installed package
version and records it in `/usr/share/netlab/component-versions`.

The runtime image check passed for
`/usr/lib/ipsec/plugins/libstrongswan-openssl.so` and OpenSSL secp384r1 key
generation. `make verify-images` also confirmed that all three task images
exist and that the strongSwan metapackage remains excluded. Certificate loading
through strongSwan is verified by the dependent VPN-150 integration test; this
image-only check does not claim an established IKE/IPsec tunnel.

Built network-node image ID:

```text
sha256:fa733d0fc8a6f6a6a3710f85e49319bf1d698bb4b54d1dac485178f3dd10c50b
```
