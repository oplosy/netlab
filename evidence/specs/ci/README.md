# CI-050 Static Quality Gate

`make ci-static` runs inside the required `policy` job on every pull request
to `main`. It needs no Docker daemon, Containerlab, or lab runtime.

| Stage | Command |
|---|---|
| Inventory schema and semantics | `make validate-inventory` |
| Python lint (E9, F, I) | `uv run --locked ruff check .` |
| YAML lint (`.yamllint.yaml`) | `uv run --locked yamllint --strict .` |
| Offline tests | `make ci-test` (unit tests, plan tests, rendered-policy tests) |
| Generated topology is current | `render_topology.py --check` |

Live tests stay skipped in CI because their `NETLAB_*_LIVE` flags are unset.
The generated `lab/*.clab.yml` and the vendored `observability/snmp-if_mib.yml`
are excluded from YAML lint; the topology is checked against its source instead.

## Local run: 2026-09-28, WSL2, uv 0.12.0, GNU Make 4.4.1

- Clean tree: `make ci-static` exited 0; 78 tests passed, 2 live tests skipped.

Negative cases (temporary inputs outside the tracked tree):

| Case | Result |
|---|---|
| Inventory with `vlan_id: "not-a-number"` | `validate-inventory` failed: `'not-a-number' is not of type 'integer'` |
| Extra test asserting `1 == 2` | `ci-test` failed |
| Untracked file with an unused import | `ci-lint` failed with `F401` |

## Not enforced yet

Style rules (E7) and `ruff format` are not enforced. The compact evidence
scripts would need a large rewrite, and that is out of scope for CI-050.
