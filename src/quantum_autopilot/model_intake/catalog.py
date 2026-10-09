"""Optional model catalog and explicit offline factory; no startup auto-loading.

This is the foundation for the product's optional model selector, not a native
UI or a runtime activation gate. Importing/listing it never opens artifacts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class ModelOption:
    model_id: str
    archive_kind: str
    status: str
    purpose: str
    default_enabled: bool = False
    runtime_available: bool = False


OPTIONS = (
    ModelOption("qkd.detector.boosting", "qkd", "offline_only", "attack classification (safe export, pickle never loaded)"),
    ModelOption("qkd.keyrate.actor", "qkd", "offline_only", "decoy-state transmitter recommendation"),
    ModelOption("qkd.sync.ridge.1s.h2s", "qkd", "offline_only", "arrival offset prediction"),
    ModelOption("qnet.routing.nominal", "qnet", "offline_only", "stochastic action scoring"),
    ModelOption("qnet.routing.prazo_longo", "qnet", "offline_only", "greedy action scoring"),
    *(ModelOption(f"qnet.compensation.{point}.{kind}", "qnet", "offline_only", "polarisation rotation prediction")
      for point in ("calibrado", "desenvolvimento") for kind in ("ridge", "mlp")),
    ModelOption("qnet.allocator.persistence", "qnet", "offline_only", "physics-based admission, no learned forecast"),
    ModelOption("qnet.allocator.ridge", "qnet", "offline_only", "physics-based admission with Ridge forecast"),
)


def model_options() -> tuple[ModelOption, ...]:
    """Availability is adapter status, NOT proof that weights are installed."""
    return OPTIONS


def open_optional_model(model_id: str | None = None, *, archive_path: str | Path | None = None,
                        mode: str = "simulated_offline"):
    """None is OFF: no model, archive access, or change to the existing baseline.

    Failure to open an explicitly selected model raises an error; there is no
    silent substitution with another checkpoint. Each open creates fresh state.
    """
    if model_id is None:
        return None
    option = next((o for o in OPTIONS if o.model_id == model_id), None)
    if option is None:
        raise ValueError("unknown optional model")
    if option.status != "offline_only":
        raise ValueError(f"model unavailable: {option.status}")
    if mode != "simulated_offline":
        raise ValueError("optional models currently admitted only for offline simulation")
    if archive_path is None:
        raise ValueError("explicit path to reviewed archive required")
    # Lazy: the application can list options or keep them OFF without weights.
    from .artifacts import ReviewedArchive
    from .inference import RoutingReplayModel, SyncReplayModel
    from .compensation import CompensationReplayModel
    from .keyrate import KeyRateReplayModel
    from .allocation import AllocationReplayModel

    archive = ReviewedArchive(archive_path, option.archive_kind)
    if model_id == "qkd.detector.boosting":
        from .detector import SafeDetector

        return SafeDetector(archive=archive)   # binds to the pickle member by hash; never deserialises it
    if model_id == "qkd.keyrate.actor":
        return KeyRateReplayModel(archive)
    if model_id == "qkd.sync.ridge.1s.h2s":
        return SyncReplayModel(archive)
    if model_id.startswith("qnet.routing."):
        return RoutingReplayModel(archive, model_id.rsplit(".", 1)[1])
    if model_id.startswith("qnet.allocator."):
        return AllocationReplayModel(archive, model_id.rsplit(".", 1)[1])
    _, _, point, architecture = model_id.split(".")
    return CompensationReplayModel(archive, point, architecture)


if __name__ == "__main__":
    print(json.dumps({"selected_model": None, "options": [asdict(o) for o in model_options()]}, indent=2))
