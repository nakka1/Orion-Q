# Local compatibility adaptations

`gmpy2-coexistence-v1.patch` records the four compatibility changes already present
in the working tree reviewed on 2026-10-05. They keep the optional GMP shim from
being mistaken for a complete `gmpy2` installation by SymPy/Qiskit and preserve
import diagnostics. They do not change physics, scientific reference data or model
weights.

Each affected file in `external/VENDORED.json` retains the author's `source_sha256`.
Its `sha256` describes the adapted file. The separate `local_patch` record identifies
the patch, its reason and SHA-256, the base commit, and the original vendored bytes
and their SHA-256. The bases in this directory include the original import renaming
already declared by the manifest; they are evidence files, not installed packages.

`tests/test_vendored_qnet.py` checks every installed file and reconstructs the exact
diff from the preserved bases. Unrecorded edits still fail integrity verification.
`tests/test_gmpy_shim_coexistence.py` exercises compatibility and import order.

When deliberately vendoring a new author release with `tools/vendor_qnet.py`, review
and reapply the compatibility patch against that release, then record its new base
and adapted hashes. Never substitute adapted hashes for author source hashes.
