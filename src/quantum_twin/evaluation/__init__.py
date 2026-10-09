"""quantum_twin.evaluation -- prediction, quantum, energy, and statistical evaluation utilities.

LAZY LOADING (ninety-second addendum, TCC item 6): applies the same
PEP 562 fix the eighty-fifth/ninety-first addenda applied to
quantum_twin/__init__.py and quantum_twin/simulation/__init__.py,
proactively to THIS subpackage -- per the ninety-first addendum's own
"Honest limitations" note, which identified this exact file as still
carrying the same latent eager-import pattern (verified there by direct
inspection: 4 eager import lines) before any migration forced
the issue reactively.
"""

import importlib

_NAME_TO_SUBMODULE = {
    "compute_confusion_matrix": "quantum_twin.evaluation.prediction",
    "compute_extended_metrics": "quantum_twin.evaluation.prediction",
    "summarize_tradeoff": "quantum_twin.evaluation.prediction",
    "compare_analytical_vs_density_matrix": "quantum_twin.evaluation.quantum",
    "EnergyConfig": "quantum_twin.evaluation.energy",
    "estimate_energy_breakdown": "quantum_twin.evaluation.energy",
    "summarize_run_energy": "quantum_twin.evaluation.energy",
    "paired_analysis": "quantum_twin.evaluation.statistics",
    "save_experiment_manifest": "quantum_twin.evaluation.statistics",
    "compute_dataset_hash": "quantum_twin.evaluation.statistics",
    "verify_dataset_hash": "quantum_twin.evaluation.statistics",
    # --- one-hundred-eleventh addendum (TCC item 6, second migration wave):
    # two real gaps found and fixed while extending this mapping --
    # CNisqResult/compute_c_nisq (eighty-first addendum's c_nisq.py) had
    # NEVER been added here at all, meaning quantum_twin.evaluation.compute_c_nisq
    # (package-level access) silently never worked, only the submodule
    # -level quantum_twin.evaluation.c_nisq.compute_c_nisq did. Fixed here
    # alongside adding the newly-migrated temporal_leakage module. ---
    "CNisqResult": "quantum_twin.evaluation.c_nisq",
    "compute_c_nisq": "quantum_twin.evaluation.c_nisq",
    "AuditResult": "quantum_twin.evaluation.temporal_leakage",
    "check_scaler_fit_matches_train_only": "quantum_twin.evaluation.temporal_leakage",
    "check_no_test_only_row_in_scaler_fit": "quantum_twin.evaluation.temporal_leakage",
    "check_future_leakage_in_window": "quantum_twin.evaluation.temporal_leakage",
    "check_train_test_target_temporal_ordering": "quantum_twin.evaluation.temporal_leakage",
    "check_four_way_split_temporal_ordering": "quantum_twin.evaluation.temporal_leakage",
    "check_window_construction_arithmetic": "quantum_twin.evaluation.temporal_leakage",
    "check_no_overlapping_target_leakage": "quantum_twin.evaluation.temporal_leakage",
    "run_full_audit": "quantum_twin.evaluation.temporal_leakage",
    "CampaignConfig": "quantum_twin.evaluation.optical_campaign",
    "OpticalCampaignResult": "quantum_twin.evaluation.optical_campaign",
    "run_optical_campaign": "quantum_twin.evaluation.optical_campaign",
    "write_campaign_artifacts": "quantum_twin.evaluation.optical_campaign",
    "OpticalTransferResult": "quantum_twin.evaluation.optical_transfer",
    "run_santanna_cross_campaign_validation": "quantum_twin.evaluation.optical_transfer",
    "write_transfer_artifacts": "quantum_twin.evaluation.optical_transfer",
    "WDMInformationConfig": "quantum_twin.evaluation.wdm_information",
    "WDMInformationResult": "quantum_twin.evaluation.wdm_information",
    "run_wdm_information_campaign": "quantum_twin.evaluation.wdm_information",
    "write_wdm_information_artifacts": "quantum_twin.evaluation.wdm_information",
    "QKDCoexistenceConfig": "quantum_twin.evaluation.qkd_coexistence",
    "QKDCoexistenceResult": "quantum_twin.evaluation.qkd_coexistence",
    "run_qkd_coexistence_campaign": "quantum_twin.evaluation.qkd_coexistence",
    "write_qkd_coexistence_artifacts": "quantum_twin.evaluation.qkd_coexistence",
    "BristolQuantumReplayConfig": "quantum_twin.evaluation.bristol_quantum_replay",
    "BristolQuantumReplayResult": "quantum_twin.evaluation.bristol_quantum_replay",
    "run_bristol_quantum_replay": "quantum_twin.evaluation.bristol_quantum_replay",
    "write_bristol_quantum_replay_artifacts": "quantum_twin.evaluation.bristol_quantum_replay",
    "QKDMultiHeadConfig": "quantum_twin.evaluation.qkd_multihead",
    "QKDMultiHeadResult": "quantum_twin.evaluation.qkd_multihead",
    "run_qkd_multihead_campaign": "quantum_twin.evaluation.qkd_multihead",
    "write_qkd_multihead_artifacts": "quantum_twin.evaluation.qkd_multihead",
    "MeasuredQuantumPredictionConfig": "quantum_twin.evaluation.measured_quantum_prediction",
    "MeasuredQuantumPredictionResult": "quantum_twin.evaluation.measured_quantum_prediction",
    "run_measured_quantum_prediction_campaign": "quantum_twin.evaluation.measured_quantum_prediction",
    "write_measured_quantum_prediction_artifacts": "quantum_twin.evaluation.measured_quantum_prediction",
    "KappaCalibrationConfig": "quantum_twin.evaluation.kappa_calibration",
    "KappaCalibrationResult": "quantum_twin.evaluation.kappa_calibration",
    "calibrate_kappa_from_measured_fidelity": "quantum_twin.evaluation.kappa_calibration",
    "BristolHCFCoexistenceConfig": "quantum_twin.evaluation.bristol_hcf_coexistence",
    "BristolHCFCoexistenceResult": "quantum_twin.evaluation.bristol_hcf_coexistence",
    "run_bristol_hcf_coexistence_campaign": "quantum_twin.evaluation.bristol_hcf_coexistence",
    "write_bristol_hcf_coexistence_artifacts": "quantum_twin.evaluation.bristol_hcf_coexistence",
    "BristolHCFRobustnessConfig": "quantum_twin.evaluation.bristol_hcf_robustness",
    "BristolHCFRobustnessResult": "quantum_twin.evaluation.bristol_hcf_robustness",
    "run_bristol_hcf_robustness_campaign": "quantum_twin.evaluation.bristol_hcf_robustness",
    "write_bristol_hcf_robustness_artifacts": "quantum_twin.evaluation.bristol_hcf_robustness",
    "DryadSTIKETemporalConfig": "quantum_twin.evaluation.dryad_stike_temporal",
    "DryadSTIKETemporalResult": "quantum_twin.evaluation.dryad_stike_temporal",
    "run_dryad_stike_temporal_campaign": "quantum_twin.evaluation.dryad_stike_temporal",
    "write_dryad_stike_temporal_artifacts": "quantum_twin.evaluation.dryad_stike_temporal",
    "build_qc_peb_capability_matrix": "quantum_twin.evaluation.qc_peb",
    "build_qc_peb_evidence_ledger": "quantum_twin.evaluation.qc_peb",
    "validate_qc_peb_capability_matrix": "quantum_twin.evaluation.qc_peb",
    "write_qc_peb_artifacts": "quantum_twin.evaluation.qc_peb",
    "CrossPlatformQKDReplicationConfig": "quantum_twin.evaluation.cross_platform_qkd_replication",
    "CrossPlatformQKDReplicationResult": "quantum_twin.evaluation.cross_platform_qkd_replication",
    "run_cross_platform_qkd_replication_campaign": "quantum_twin.evaluation.cross_platform_qkd_replication",
    "write_cross_platform_qkd_replication_artifacts": "quantum_twin.evaluation.cross_platform_qkd_replication",
    "PublicQKDEvidenceResult": "quantum_twin.evaluation.public_qkd_evidence",
    "analyze_published_aggregate_observations": "quantum_twin.evaluation.public_qkd_evidence",
    "build_public_qkd_candidate_registry": "quantum_twin.evaluation.public_qkd_evidence",
    "build_search_protocol": "quantum_twin.evaluation.public_qkd_evidence",
    "load_published_aggregate_observations": "quantum_twin.evaluation.public_qkd_evidence",
    "run_public_qkd_evidence_audit": "quantum_twin.evaluation.public_qkd_evidence",
    "validate_public_qkd_candidate_registry": "quantum_twin.evaluation.public_qkd_evidence",
    "write_public_qkd_evidence_artifacts": "quantum_twin.evaluation.public_qkd_evidence",
    "ClassicalQuantumParetoConfig": "quantum_twin.evaluation.classical_quantum_pareto",
    "ClassicalQuantumParetoResult": "quantum_twin.evaluation.classical_quantum_pareto",
    "pareto_front_mask": "quantum_twin.evaluation.classical_quantum_pareto",
    "run_classical_quantum_pareto_campaign": "quantum_twin.evaluation.classical_quantum_pareto",
    "write_classical_quantum_pareto_artifacts": "quantum_twin.evaluation.classical_quantum_pareto",
    "QKDParetoFalsificationConfig": "quantum_twin.evaluation.qkd_pareto_falsification",
    "QKDParetoFalsificationResult": "quantum_twin.evaluation.qkd_pareto_falsification",
    "joint_order_metrics": "quantum_twin.evaluation.qkd_pareto_falsification",
    "run_qkd_pareto_falsification_campaign": "quantum_twin.evaluation.qkd_pareto_falsification",
    "write_qkd_pareto_falsification_artifacts": "quantum_twin.evaluation.qkd_pareto_falsification",
    "QKDDoseOrderFalsificationConfig": "quantum_twin.evaluation.qkd_dose_order_falsification",
    "QKDDoseOrderFalsificationResult": "quantum_twin.evaluation.qkd_dose_order_falsification",
    "build_monotonic_baseline_predictions": "quantum_twin.evaluation.qkd_dose_order_falsification",
    "run_qkd_dose_order_falsification_campaign": "quantum_twin.evaluation.qkd_dose_order_falsification",
    "write_qkd_dose_order_falsification_artifacts": "quantum_twin.evaluation.qkd_dose_order_falsification",
    "QKDHierarchicalRobustnessConfig": "quantum_twin.evaluation.qkd_hierarchical_robustness",
    "QKDHierarchicalRobustnessResult": "quantum_twin.evaluation.qkd_hierarchical_robustness",
    "audit_campaign_design": "quantum_twin.evaluation.qkd_hierarchical_robustness",
    "run_qkd_hierarchical_robustness_campaign": "quantum_twin.evaluation.qkd_hierarchical_robustness",
    "write_qkd_hierarchical_robustness_artifacts": "quantum_twin.evaluation.qkd_hierarchical_robustness",
    "ThesisClaimValidationResult": "quantum_twin.evaluation.thesis_claims",
    "load_claim_registry": "quantum_twin.evaluation.thesis_claims",
    "validate_claim_registry": "quantum_twin.evaluation.thesis_claims",
    "write_claim_validation_artifacts": "quantum_twin.evaluation.thesis_claims",
    "TCCEvidenceBundleResult": "quantum_twin.evaluation.tcc_evidence_bundle",
    "build_tcc_evidence_bundle": "quantum_twin.evaluation.tcc_evidence_bundle",
}


def __getattr__(name):
    """PEP 562: called only when normal attribute lookup fails -- i.e.
    the FIRST time this specific name is accessed. Imports ONLY the one
    submodule that name actually lives in, not the others."""
    if name in _NAME_TO_SUBMODULE:
        module = importlib.import_module(_NAME_TO_SUBMODULE[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'quantum_twin.evaluation' has no attribute '{name}'")


def __dir__():
    return sorted(_NAME_TO_SUBMODULE.keys())


__all__ = list(_NAME_TO_SUBMODULE.keys())
