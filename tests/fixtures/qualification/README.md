# Archived qualification fixtures

These fixtures make contract regressions independent of ignored `outputs/` and `work/` directories. `provenance.json` records the exact archived bytes and SHA-256 of each source. Tests extract into a new temporary directory and never import unverified author code.

The SeQUeNCe census/report/matrix are historical public OrionQ artifacts from 2026-10-10, retained byte for byte. They check coverage derivation; they do not execute SeQUeNCe or qualify the expanded current network.

The optical reference archive is the MIT-licensed upstream v1.1.0 from [Zenodo](https://doi.org/10.5281/zenodo.6384558), SHA-256 `895b3b093b33587d08c9d878c94597825ad0d83dae564a34e6455b673146f482`. Its license is reproduced alongside. The tests preserve the production pinned hash, require all 67 upstream files to match and exercise tampering before importing code.
