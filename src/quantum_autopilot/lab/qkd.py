"""Identified, reproducible single-photon BB84 demonstration, never certified QKD.

The centralized emulator knows both endpoint bit strings. PCG64 is intentionally
reproducible and is not a qualified cryptographic RNG. Returned material is only
for exercising the local key-delivery interface; it must never protect real data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

MODEL_ID = "orion.lab.bb84.observed-ideal-single-photon.v2"


@dataclass(frozen=True)
class QkdConfig:
    seed: int = 20261007
    emitted_pulses: int = 80000
    acquisition_s: float = 1.0
    channel_loss_db: float = 3.0
    detector_efficiency: float = 0.7
    bit_error_probability: float = 0.01
    parameter_sample_fraction: float = 0.25
    parameter_estimation_error: float = 1e-10
    privacy_amplification_error: float = 1e-10
    verification_bits: int = 64
    coincidence_window_ps: int = 1000
    expected_flight_time_ps: int = 0
    timing_alignment_tolerance_ps: int = 100
    authentication_cost_bits: int = 0

    def validate(self):
        for name in ("seed", "emitted_pulses", "verification_bits", "coincidence_window_ps",
                     "expected_flight_time_ps", "timing_alignment_tolerance_ps", "authentication_cost_bits"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} requires a nonnegative integer")
        if not 100 <= self.emitted_pulses <= 1000000:
            raise ValueError("emitted_pulses must be between 100 and 1000000")
        if not 32 <= self.verification_bits <= 128:
            raise ValueError("verification_bits must be between 32 and 128")
        if not 1 <= self.coincidence_window_ps <= 10**12:
            raise ValueError("coincidence_window_ps must be 1 through 10^12 ps")
        if self.expected_flight_time_ps >= 2**63 or self.timing_alignment_tolerance_ps > self.coincidence_window_ps // 2:
            raise ValueError("flight time or clock alignment tolerance invalid")
        for name in ("acquisition_s", "channel_loss_db", "detector_efficiency", "bit_error_probability",
                     "parameter_sample_fraction", "parameter_estimation_error", "privacy_amplification_error"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} requires a finite number")
        if not 0 < self.acquisition_s <= 3600 or not 0 <= self.channel_loss_db <= 100:
            raise ValueError("invalid acquisition duration or optical loss")
        if round(self.acquisition_s * 1e12) < 2 * self.emitted_pulses:
            raise ValueError("acquisition duration cannot resolve preparation and detection events in ps")
        if not 0 <= self.detector_efficiency <= 1 or not 0 <= self.bit_error_probability <= 0.5:
            raise ValueError("invalid detector efficiency or bit error probability")
        if not 0 < self.parameter_sample_fraction < 1:
            raise ValueError("parameter_sample_fraction must be between zero and one")
        if not all(0 < value < 0.01 for value in
                   (self.parameter_estimation_error, self.privacy_amplification_error)):
            raise ValueError("invalid declared statistical error probability")


@dataclass(frozen=True)
class QkdSession:
    summary: dict
    # Repr and evidence serialization never expose the emulated secret bytes.
    delivery_material: bytes = field(repr=False)


def run_bb84(config: QkdConfig | None = None, *, experiment_id="orion-lab-qkd-001", observation_path=None,
             security=None, principal=None, authenticated_channel=None) -> QkdSession:
    """Acquire identified pulse observations, then run the shared protocol pipeline."""
    from .observations import synthetic_bb84_observations
    from .observed_qkd import run_observed_bb84
    config = config or QkdConfig()
    config.validate()
    block = synthetic_bb84_observations(seed=config.seed, count=config.emitted_pulses,
        loss_db=config.channel_loss_db, efficiency=config.detector_efficiency,
        error_probability=config.bit_error_probability, experiment_id=experiment_id,
        duration_ps=round(config.acquisition_s * 1e12))
    if observation_path is not None:
        from .observations import persist_observation_block
        persist_observation_block(block, observation_path)
    if security is not None:
        security.authorize("protocol.process", "protocol:" + experiment_id, principal)
        if authenticated_channel is None:
            from uuid import uuid4
            from quantum_autopilot.security.protocol import AuthenticatedClassicalChannel
            # Independent vault material is provisioned only by this trusted
            # local workflow, never imported from the emulated raw/key bits.
            key_reference = "qkd-classical-authentication-v1"
            security.vault.get_or_create_bytes(key_reference, size=32)
            authenticated_channel = AuthenticatedClassicalChannel(
                security.root / "protocol" / "classical.sqlite", session_id=uuid4().hex,
                sender="lab-preparation", receiver="lab-analysis",
                transcript_binding=block.observation_sha256, key_reference=key_reference,
                vault=security.vault, origin=block.origin, mode=block.mode,
                runtime=security, principal=principal)
    if security is not None:
        # Only local, unqualified computation occurs inside this context.
        # No capture, device setter, material buffer or delivery is included.
        # Classical anti-replay state is atomic for this local computational
        # block; no result escapes before commit and every authorization audit
        # is authenticated/checkpointed BEFORE return. External channel traffic
        # retains a durable boundary per message.
        with security.audit.batch(), authenticated_channel.atomic_processing():
            return run_observed_bb84(block, config, authenticated_channel=authenticated_channel)
    return run_observed_bb84(block, config, authenticated_channel=authenticated_channel)


def run_qkd_demo(output_directory, *, config=None, security=None, principal=None):
    """Lazy optional dependency boundary used by the laboratory service."""
    from .kms import run_qkd_demo as execute
    return execute(output_directory, config=config, security=security, principal=principal)
