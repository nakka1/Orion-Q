"""Local adapter declarations, not device authentication or physical authority."""
from dataclasses import dataclass


OPERATIONS = {
    "select_node": "view", "select_link": "view", "reload": "view",
    "export_record": "local_export", "acknowledge": "local_note",
    "stage_node": "local_staging", "remove_staged_node": "local_staging",
    "pause": "synthetic_stream", "dropout": "synthetic_stream", "cancel": "simulation",
    "run_scenario": "simulation", "recalibrate": "shadow_diagnostic",
    "replay_model": "shadow_replay", "set_decision_source": "policy_binding",
    "add_simulated_node": "simulation_inventory",
    "configure_simulated_link": "simulation_inventory",
    "apply_simulated_node_action": "simulation_inventory",
    "run_workspace_path": "simulation",
    "run_lab_experiment": "simulation",
    "configure_laboratory": "simulation",
    "run_temporal_path": "simulation",
    "run_temporal_campaign": "simulation",
    "read_temporal_campaign_history": "view",
    "read_console_commands": "view",
    "review_policy_preparation": "policy_preparation",
    "approve_policy_proposal": "policy_approval",
    "activate_simulation_policy": "policy_activation",
    "read_active_policy": "view",
    "read_policy_decisions": "view",
    "observe_policy_context": "view",
    "prepare_policy_rollback": "policy_preparation",
    "recover_simulation_policy": "policy_recovery",
    # Live network (docs/product/orion_q_live_network_design.md §1–§2); simulator only.
    "add_network_node": "simulation_inventory",
    "set_node_location": "simulation_inventory",
    "set_node_isolation": "simulation_inventory",
    "set_link_active": "simulation_inventory",
    "recalibrate_node": "simulation",
    "request_pair": "simulation",
    "purify_pair": "simulation",
    "consume_pair": "simulation",
    "define_pair": "simulation",
    "remove_pair": "simulation",
    "inject_input_event": "simulated_input",
    "configure_network_input": "simulation",
    "configure_technology": "simulation",
    "save_communicator": "simulation_inventory",
    "configure_communicator_connection": "simulation_inventory",
    "configure_technology_source": "simulation",
    "provision_satellite_session": "security_administration",
    "export_technology_events": "local_export",
    "set_model_archive": "local_staging",
    "set_ml_control": "simulation",
    "read_network_live": "view",
    "register_fpga_device": "simulation_inventory",
    "configure_fpga_device": "simulation_inventory",
    "set_fpga_connection": "simulation_inventory",
    "inject_fpga_fault": "simulated_input",
}


#: Live-network operations of a service that carries a network engine (any input source).
LIVE_NETWORK_OPERATIONS = (
    "read_network_live", "add_network_node", "set_node_location", "set_node_isolation", "set_link_active",
    "recalibrate_node", "define_pair", "remove_pair", "request_pair", "purify_pair", "consume_pair",
    "set_model_archive", "set_ml_control", "configure_network_input",
    "configure_technology", "configure_technology_source", "export_technology_events",
    "save_communicator", "configure_communicator_connection", "provision_satellite_session",
    "register_fpga_device", "configure_fpga_device", "set_fpga_connection")
#: Declared only by the demonstration: its INPUT is simulated, so input events can be injected.
DEMO_ONLY_OPERATIONS = ("inject_input_event", "inject_fpga_fault")


@dataclass(frozen=True)
class SourceCapabilities:
    adapter_id: str = "undeclared"
    adapter_version: str = "1"
    mode: str = "unavailable"
    health: str = "unavailable"
    operations: tuple[str, ...] = ()
    limits: tuple[tuple[str, int], ...] = ()
    note: str = "No adapter capabilities declared; commands are disabled."
    schema: str = "orion.capabilities.v1"

    def __post_init__(self):
        if self.mode not in {"unavailable", "read_only", "synthetic", "simulation"}:
            raise ValueError("unsupported execution mode; physical control is unavailable")
        if self.health not in {"ready", "degraded", "unavailable"}:
            raise ValueError("invalid adapter health")
        if type(self.operations) is not tuple or len(set(self.operations)) != len(self.operations):
            raise ValueError("operations must be a unique immutable tuple")
        if any(op not in OPERATIONS for op in self.operations):
            raise ValueError("unknown or physical operation")
        if self.mode in {"read_only", "unavailable"} and any(
            OPERATIONS[op] not in {"view", "local_export"} for op in self.operations
        ):
            raise ValueError("read-only sources cannot declare control operations")
        if type(self.limits) is not tuple or any(
            type(item) is not tuple or len(item) != 2 or not isinstance(item[0], str)
            or type(item[1]) is not int or item[1] < 0 for item in self.limits
        ):
            raise ValueError("limits must be immutable named non-negative integers")

    def allows(self, operation: str) -> bool:
        return operation in self.operations

    def require(self, operation: str) -> None:
        if not self.allows(operation):
            raise ValueError(f"Command {operation!r} unavailable for {self.adapter_id} ({self.mode}). {self.note}")

    def describe(self) -> str:
        commands = ", ".join(self.operations) or "none"
        limits = ", ".join(f"{name}={value}" for name, value in self.limits) or "not declared"
        return (f"{self.adapter_id} v{self.adapter_version} · {self.mode.upper()} · {self.health.upper()}\n"
                f"Contract: {self.schema}\nOperations: {commands}\nLimits: {limits}\n"
                f"{self.note}\nPhysical actuation: unavailable")


def synthetic_capabilities(*, degraded=False):
    return SourceCapabilities(
        adapter_id="synthetic-stream", mode="synthetic", health="degraded" if degraded else "ready",
        operations=("select_node", "pause", "dropout", "cancel", "run_scenario", "recalibrate",
                    "export_record", "acknowledge", "reload", "stage_node", "remove_staged_node",
                    "apply_simulated_node_action", "configure_simulated_link"),
        limits=(("display_nodes", 32), ("display_links", 256)),
        note="Developer UI fixture, not the demonstration: artificial display values and isolated controller tests. No WDM model replay or runtime policy switching.")
