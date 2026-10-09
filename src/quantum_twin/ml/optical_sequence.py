"""Optical-regression adaptations of the repository's original Edge models.

The recurrent/convolutional backbones are inherited unchanged from EdgeLSTM,
EdgeGRU and EdgeTCN.  Only the fidelity-specific sigmoid output is replaced by
an identity map so the same compact edge architectures can regress OSNR or
log10(BER).  This keeps the new experiment tied to the original thesis models
without pretending that the Sant'Anna dataset contains quantum fidelity.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from quantum_twin.ml.architectures import EdgeGRU, EdgeTCN
from quantum_twin.ml.lstm import EdgeLSTM


class OpticalEdgeLSTM(EdgeLSTM):
    """Original EdgeLSTM backbone with a real-valued regression output."""

    def __init__(self, input_size: int, hidden_size: int = 16, num_layers: int = 1):
        super().__init__(input_size=input_size, hidden_size=hidden_size, num_layers=num_layers)
        self.activation = nn.Identity()


class OpticalEdgeGRU(EdgeGRU):
    """Original EdgeGRU backbone with a real-valued regression output."""

    def __init__(self, input_size: int, hidden_size: int = 16, num_layers: int = 1):
        super().__init__(input_size=input_size, hidden_size=hidden_size, num_layers=num_layers)
        self.activation = nn.Identity()


class OpticalEdgeTCN(EdgeTCN):
    """Original strictly-causal EdgeTCN with a real-valued output."""

    def __init__(
        self,
        input_size: int,
        hidden_size: int = 16,
        kernel_size: int = 3,
        num_layers: int = 3,
    ):
        super().__init__(
            input_size=input_size,
            hidden_channels=hidden_size,
            kernel_size=kernel_size,
            num_layers=num_layers,
        )
        self.activation = nn.Identity()


OPTICAL_SEQUENCE_MODELS = ("edge_lstm", "edge_gru", "edge_tcn")


def make_optical_sequence_model(
    model_name: str,
    *,
    input_size: int,
    hidden_size: int = 16,
) -> nn.Module:
    """Construct one of the optical adaptations with a common interface."""
    constructors = {
        "edge_lstm": OpticalEdgeLSTM,
        "edge_gru": OpticalEdgeGRU,
        "edge_tcn": OpticalEdgeTCN,
    }
    if model_name not in constructors:
        raise ValueError(
            f"unknown optical sequence model {model_name!r}; "
            f"expected one of {OPTICAL_SEQUENCE_MODELS}"
        )
    return constructors[model_name](input_size=input_size, hidden_size=hidden_size)


@dataclass(frozen=True)
class OpticalTrainingSummary:
    best_validation_loss: float
    epochs_trained: int
    stopped_early: bool


def train_optical_sequence_model(
    model: nn.Module,
    X_train: np.ndarray,
    y_train: np.ndarray,
    *,
    seed: int = 42,
    max_epochs: int = 50,
    batch_size: int = 256,
    validation_fraction: float = 0.15,
    patience: int = 8,
    learning_rate: float = 1e-3,
    device: str | torch.device = "cpu",
) -> tuple[nn.Module, OpticalTrainingSummary]:
    """Train with a temporal validation tail, Huber loss and early stopping.

    Inputs and targets are expected to be scaled using training-only
    statistics by the caller.  Keeping preprocessing outside the model makes
    its fit scope explicit in experiment manifests and tests.
    """
    X = np.asarray(X_train, dtype=np.float32)
    y = np.asarray(y_train, dtype=np.float32).reshape(-1, 1)
    if X.ndim != 3 or len(X) != len(y):
        raise ValueError("X_train must be [samples, time, features] and align with y_train")
    if len(X) < 4:
        raise ValueError("at least four training windows are required")
    if max_epochs < 1 or batch_size < 1 or patience < 1:
        raise ValueError("max_epochs, batch_size and patience must be >= 1")
    if not 0.0 < validation_fraction < 0.5:
        raise ValueError("validation_fraction must be between 0 and 0.5")

    torch.manual_seed(int(seed))
    np.random.seed(int(seed))
    execution_device = torch.device(device)
    model = model.to(execution_device)

    validation_count = max(1, int(np.floor(len(X) * validation_fraction)))
    split = len(X) - validation_count
    X_fit = torch.from_numpy(X[:split])
    y_fit = torch.from_numpy(y[:split])
    X_validation = torch.from_numpy(X[split:]).to(execution_device)
    y_validation = torch.from_numpy(y[split:]).to(execution_device)
    generator = torch.Generator().manual_seed(int(seed))
    loader = DataLoader(
        TensorDataset(X_fit, y_fit),
        batch_size=min(int(batch_size), len(X_fit)),
        shuffle=True,
        generator=generator,
    )

    criterion = nn.SmoothL1Loss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(learning_rate))
    best_loss = float("inf")
    best_state = copy.deepcopy(model.state_dict())
    epochs_without_improvement = 0
    epochs_trained = 0

    for epoch in range(int(max_epochs)):
        model.train()
        for batch_X, batch_y in loader:
            batch_X = batch_X.to(execution_device)
            batch_y = batch_y.to(execution_device)
            optimizer.zero_grad()
            loss = criterion(model(batch_X), batch_y)
            loss.backward()
            optimizer.step()

        model.eval()
        with torch.no_grad():
            validation_loss = float(criterion(model(X_validation), y_validation).item())
        epochs_trained = epoch + 1
        if validation_loss < best_loss - 1e-7:
            best_loss = validation_loss
            best_state = copy.deepcopy(model.state_dict())
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= int(patience):
                break

    model.load_state_dict(best_state)
    model.eval()
    return model, OpticalTrainingSummary(
        best_validation_loss=best_loss,
        epochs_trained=epochs_trained,
        stopped_early=epochs_trained < int(max_epochs),
    )


def predict_optical_sequence_model(
    model: nn.Module,
    X: np.ndarray,
    *,
    batch_size: int = 1024,
    device: str | torch.device = "cpu",
) -> np.ndarray:
    """Return one real-valued prediction per window."""
    values = np.asarray(X, dtype=np.float32)
    if values.ndim != 3:
        raise ValueError("X must be [samples, time, features]")
    execution_device = torch.device(device)
    model = model.to(execution_device)
    model.eval()
    predictions: list[np.ndarray] = []
    with torch.no_grad():
        for start in range(0, len(values), int(batch_size)):
            batch = torch.from_numpy(values[start:start + int(batch_size)]).to(execution_device)
            predictions.append(model(batch).cpu().numpy().reshape(-1))
    return np.concatenate(predictions) if predictions else np.empty(0, dtype=float)


__all__ = [
    "OPTICAL_SEQUENCE_MODELS",
    "OpticalEdgeGRU",
    "OpticalEdgeLSTM",
    "OpticalEdgeTCN",
    "OpticalTrainingSummary",
    "make_optical_sequence_model",
    "predict_optical_sequence_model",
    "train_optical_sequence_model",
]
