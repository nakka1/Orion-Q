"""Public sandbox API; optional inference/simulation imports resolve on demand."""
from __future__ import annotations

from importlib import import_module

# PEP 562 is also used by quantum_twin and qnet_qkd. Keep the public
# names and their defining objects unchanged; importing this namespace
# alone must not initialize PyTorch, Qiskit/Aer or training libraries.
_EXPORTS = {
    'CONTROL_PLANE_SCHEMA_VERSION': ('quantum_autopilot.control_plane.contracts', 'CONTROL_PLANE_SCHEMA_VERSION'),
    'ControlAction': ('quantum_autopilot.control_plane.contracts', 'ControlAction'),
    'ControlCycleRecord': ('quantum_autopilot.control_plane.contracts', 'ControlCycleRecord'),
    'ControlExecutionResult': ('quantum_autopilot.control_plane.contracts', 'ControlExecutionResult'),
    'ControlIntent': ('quantum_autopilot.control_plane.contracts', 'ControlIntent'),
    'ControlMode': ('quantum_autopilot.control_plane.contracts', 'ControlMode'),
    'NetworkStateSnapshot': ('quantum_autopilot.control_plane.contracts', 'NetworkStateSnapshot'),
    'SafetyAssessment': ('quantum_autopilot.control_plane.contracts', 'SafetyAssessment'),
    'SafetyViolation': ('quantum_autopilot.control_plane.contracts', 'SafetyViolation'),
    'COMPARATIVE_EVALUATION_SCHEMA_VERSION': ('quantum_autopilot.control_plane.comparative_evaluation', 'COMPARATIVE_EVALUATION_SCHEMA_VERSION'),
    'ComparativeControlConfig': ('quantum_autopilot.control_plane.comparative_evaluation', 'ComparativeControlConfig'),
    'audit_repeater_policy_saturation': ('quantum_autopilot.control_plane.comparative_evaluation', 'audit_repeater_policy_saturation'),
    'evaluate_controller_predictions': ('quantum_autopilot.control_plane.comparative_evaluation', 'evaluate_controller_predictions'),
    'holm_adjust': ('quantum_autopilot.control_plane.comparative_evaluation', 'holm_adjust'),
    'paired_sign_flip_test': ('quantum_autopilot.control_plane.comparative_evaluation', 'paired_sign_flip_test'),
    'DryRunExecutor': ('quantum_autopilot.control_plane.executors', 'DryRunExecutor'),
    'SandboxExecutor': ('quantum_autopilot.control_plane.executors', 'SandboxExecutor'),
    'SimulatedQuantumExecutor': ('quantum_autopilot.control_plane.executors', 'SimulatedQuantumExecutor'),
    'ControlJournal': ('quantum_autopilot.control_plane.journal', 'ControlJournal'),
    'LoadedWDMModelBundle': ('quantum_autopilot.control_plane.model_bundle', 'LoadedWDMModelBundle'),
    'MAX_REPLAY_RECORDS': ('quantum_autopilot.control_plane.model_bundle', 'MAX_REPLAY_RECORDS'),
    'WDMReplayRecord': ('quantum_autopilot.control_plane.model_bundle', 'WDMReplayRecord'),
    'WDM_MODEL_BUNDLE_ARTIFACT_TYPE': ('quantum_autopilot.control_plane.model_bundle', 'WDM_MODEL_BUNDLE_ARTIFACT_TYPE'),
    'WDM_MODEL_BUNDLE_SCHEMA_VERSION': ('quantum_autopilot.control_plane.model_bundle', 'WDM_MODEL_BUNDLE_SCHEMA_VERSION'),
    'export_wdm_model_bundle': ('quantum_autopilot.control_plane.model_bundle', 'export_wdm_model_bundle'),
    'load_wdm_model_bundle': ('quantum_autopilot.control_plane.model_bundle', 'load_wdm_model_bundle'),
    'HardwareInterlock': ('quantum_autopilot.control_plane.physical', 'HardwareInterlock'),
    'InterlockState': ('quantum_autopilot.control_plane.physical', 'InterlockState'),
    'PHYSICAL_ACTION_GATES': ('quantum_autopilot.control_plane.physical', 'PHYSICAL_ACTION_GATES'),
    'PhysicalAdapter': ('quantum_autopilot.control_plane.physical', 'PhysicalAdapter'),
    'PhysicalAuthorization': ('quantum_autopilot.control_plane.physical', 'PhysicalAuthorization'),
    'PhysicalExecutor': ('quantum_autopilot.control_plane.physical', 'PhysicalExecutor'),
    'REALTIME_TIER_ACTIONS': ('quantum_autopilot.control_plane.physical', 'REALTIME_TIER_ACTIONS'),
    'authorize_physical_action': ('quantum_autopilot.control_plane.physical', 'authorize_physical_action'),
    'RepeaterSandboxPolicy': ('quantum_autopilot.control_plane.policy', 'RepeaterSandboxPolicy'),
    'ControllerRuntime': ('quantum_autopilot.control_plane.runtime', 'ControllerRuntime'),
    'SafetyPolicyConfig': ('quantum_autopilot.control_plane.safety', 'SafetyPolicyConfig'),
    'SafetySupervisor': ('quantum_autopilot.control_plane.safety', 'SafetySupervisor'),
    'TASK_POLICY_SCHEMA_VERSION': ('quantum_autopilot.control_plane.task_constrained_policy', 'TASK_POLICY_SCHEMA_VERSION'),
    'AbstentionCalibrationConfig': ('quantum_autopilot.control_plane.task_constrained_policy', 'AbstentionCalibrationConfig'),
    'ControlTask': ('quantum_autopilot.control_plane.task_constrained_policy', 'ControlTask'),
    'FrozenTaskPolicySpec': ('quantum_autopilot.control_plane.task_constrained_policy', 'FrozenTaskPolicySpec'),
    'TaskConstrainedRepeaterPolicy': ('quantum_autopilot.control_plane.task_constrained_policy', 'TaskConstrainedRepeaterPolicy'),
    'audit_task_policy_sensitivity': ('quantum_autopilot.control_plane.task_constrained_policy', 'audit_task_policy_sensitivity'),
    'calibrate_link_policy_spec': ('quantum_autopilot.control_plane.task_constrained_policy', 'calibrate_link_policy_spec'),
    'evaluate_link_policy_predictions': ('quantum_autopilot.control_plane.task_constrained_policy', 'evaluate_link_policy_predictions'),
    'DEFAULT_SCENARIO_PRESET': ('quantum_autopilot.control_plane.topology', 'DEFAULT_SCENARIO_PRESET'),
    'DEFAULT_TOPOLOGY_ID': ('quantum_autopilot.control_plane.topology', 'DEFAULT_TOPOLOGY_ID'),
    'NodeRole': ('quantum_autopilot.control_plane.topology', 'NodeRole'),
    'ScenarioDefinition': ('quantum_autopilot.control_plane.topology', 'ScenarioDefinition'),
    'ScenarioStep': ('quantum_autopilot.control_plane.topology', 'ScenarioStep'),
    'SimulatedLink': ('quantum_autopilot.control_plane.topology', 'SimulatedLink'),
    'SimulatedNodeState': ('quantum_autopilot.control_plane.topology', 'SimulatedNodeState'),
    'SimulatedTopology': ('quantum_autopilot.control_plane.topology', 'SimulatedTopology'),
    'SimulatedTopologyStore': ('quantum_autopilot.control_plane.topology', 'SimulatedTopologyStore'),
    'TOPOLOGY_SCHEMA_VERSION': ('quantum_autopilot.control_plane.topology', 'TOPOLOGY_SCHEMA_VERSION'),
    'TopologyScenarioRunner': ('quantum_autopilot.control_plane.topology', 'TopologyScenarioRunner'),
    'default_simulated_topology': ('quantum_autopilot.control_plane.topology', 'default_simulated_topology'),
    'resilience_demo_scenario': ('quantum_autopilot.control_plane.topology', 'resilience_demo_scenario'),
    'WDMControlRequest': ('quantum_autopilot.control_plane.wdm_inference', 'WDMControlRequest'),
    'WDMFidelityPrediction': ('quantum_autopilot.control_plane.wdm_inference', 'WDMFidelityPrediction'),
    'WDMModelDrivenController': ('quantum_autopilot.control_plane.wdm_inference', 'WDMModelDrivenController'),
    'WDMModelInferenceAdapter': ('quantum_autopilot.control_plane.wdm_inference', 'WDMModelInferenceAdapter'),
    'WDMModelOutputKind': ('quantum_autopilot.control_plane.wdm_inference', 'WDMModelOutputKind'),
    'WDMModelSpec': ('quantum_autopilot.control_plane.wdm_inference', 'WDMModelSpec'),
    'WDMTelemetryPoint': ('quantum_autopilot.control_plane.wdm_inference', 'WDMTelemetryPoint'),
    'WDM_INFERENCE_SCHEMA_VERSION': ('quantum_autopilot.control_plane.wdm_inference', 'WDM_INFERENCE_SCHEMA_VERSION'),
    'hash_torch_model': ('quantum_autopilot.control_plane.wdm_inference', 'hash_torch_model'),
}

__all__ = ['CONTROL_PLANE_SCHEMA_VERSION', 'COMPARATIVE_EVALUATION_SCHEMA_VERSION', 'TASK_POLICY_SCHEMA_VERSION', 'AbstentionCalibrationConfig', 'DEFAULT_SCENARIO_PRESET', 'DEFAULT_TOPOLOGY_ID', 'ControlAction', 'ComparativeControlConfig', 'ControlCycleRecord', 'ControlExecutionResult', 'ControlIntent', 'ControlJournal', 'ControlMode', 'ControlTask', 'ControllerRuntime', 'DryRunExecutor', 'FrozenTaskPolicySpec', 'HardwareInterlock', 'InterlockState', 'LoadedWDMModelBundle', 'MAX_REPLAY_RECORDS', 'NetworkStateSnapshot', 'NodeRole', 'PHYSICAL_ACTION_GATES', 'PhysicalAdapter', 'PhysicalAuthorization', 'PhysicalExecutor', 'REALTIME_TIER_ACTIONS', 'RepeaterSandboxPolicy', 'SafetyAssessment', 'SafetyPolicyConfig', 'SafetySupervisor', 'SafetyViolation', 'SandboxExecutor', 'ScenarioDefinition', 'ScenarioStep', 'SimulatedQuantumExecutor', 'SimulatedLink', 'SimulatedNodeState', 'SimulatedTopology', 'SimulatedTopologyStore', 'TOPOLOGY_SCHEMA_VERSION', 'TaskConstrainedRepeaterPolicy', 'TopologyScenarioRunner', 'WDMControlRequest', 'WDMFidelityPrediction', 'WDMReplayRecord', 'WDM_MODEL_BUNDLE_ARTIFACT_TYPE', 'WDM_MODEL_BUNDLE_SCHEMA_VERSION', 'WDMModelDrivenController', 'WDMModelInferenceAdapter', 'WDMModelOutputKind', 'WDMModelSpec', 'WDMTelemetryPoint', 'WDM_INFERENCE_SCHEMA_VERSION', 'audit_repeater_policy_saturation', 'audit_task_policy_sensitivity', 'authorize_physical_action', 'calibrate_link_policy_spec', 'default_simulated_topology', 'evaluate_controller_predictions', 'evaluate_link_policy_predictions', 'export_wdm_model_bundle', 'hash_torch_model', 'holm_adjust', 'load_wdm_model_bundle', 'paired_sign_flip_test', 'resilience_demo_scenario']


def __getattr__(name):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, symbol = target
    value = getattr(import_module(module_name), symbol)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
