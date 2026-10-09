# Bristol multivendor NDFF WDM telemetry

## Source and reuse boundary

- Source: [University of Bristol public repository](https://github.com/hpn-bristol/Open-Source-Data-for-Multi-Domain-Network-Monitoring-and-Sensing-in-Optical-and-Wireless-Networks),
  “Open-Source Data for Multi-Domain Network Monitoring and Sensing in
  Optical and Wireless Networks”.
- Field path: 339 km National Dark Fibre Facility link.
- Sampling used here: publisher-provided 15-minute aggregates from ADVA,
  Ciena and Voyager equipment, 26 December 2023 through 26 January 2024.
- License notice: academic research and non-commercial use. This is more
  restrictive than the Apache-2.0 source-code license; the telemetry is not
  redistributed in this repository.

The three Git-LFS objects used by the reference run were verified by SHA-256.
Full hashes are stored in the generated run report. The canonical adapter
produces 9,623 rows across seven optical channels with pre-FEC BER, Q-factor
and, where the vendor exports it, received optical power. The source does not
declare a timezone, so `timestamp_local_unzoned` deliberately remains naive;
UTC is not invented.

## Quantum replay boundary

Bristol has no measured quantum target. `run_bristol_quantum_replay.py`
inserts each recorded channel BER into the repository's original

`depol = clip(DEPOLARIZATION_P + KAPPA_DEPOL_FROM_BER * BER, 0, 0.5)`

and evaluates `QuantumChannel.simulate_fidelity` with Qiskit Aer. A dense Aer
grid is interpolated; the reference run's maximum audited absolute
interpolation error was `2.94e-8`.

This makes the direct BER/fidelity relationship circular by construction.
The non-circular test instead predicts one channel's future simulated
fidelity from its own fidelity history plus *other channels' recorded WDM
history*, and requires the combined model to beat both an autoregressive
baseline and a within-device temporally permuted-WDM control.

```bash
python experiments/run_bristol_quantum_replay.py \
  --dataset-dir "/path/to/OTN Monitoring Data_one_month" \
  --output-dir outputs/bristol_quantum_replay \
  --horizons 1 4 \
  --models ridge edge_lstm edge_gru edge_tcn
```

The EdgeLSTM, EdgeGRU and strictly causal EdgeTCN backbones are the original
repository models, adapted only with the already-established real-valued
regression head. See
[`../results/quantum_wdm_evidence.md`](../results/quantum_wdm_evidence.md).
