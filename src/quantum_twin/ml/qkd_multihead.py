"""Dual/TriHead models for measured QKD coexistence outcomes.

These models transfer the original DualHead *factorization* to measured QKD:
secure-key availability is separated from conditional key rate, while the
TriHead adds QBER.  The current public sweeps are tabular controlled cells,
not timestamped sequences, so an MLP trunk is used instead of fabricating an
LSTM time axis.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class _QKDSharedTrunk(nn.Module):
    def __init__(self, input_size: int, hidden_size: int):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(int(input_size), int(hidden_size)),
            nn.SiLU(),
            nn.LayerNorm(int(hidden_size)),
            nn.Linear(int(hidden_size), int(hidden_size)),
            nn.SiLU(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x)


class QKDDualHead(nn.Module):
    """Predict P(SKR>0) and log-SKR conditional on key availability."""

    def __init__(self, input_size: int, hidden_size: int = 12):
        super().__init__()
        self.trunk = _QKDSharedTrunk(input_size, hidden_size)
        self.availability_head = nn.Linear(hidden_size, 1)
        self.conditional_skr_head = nn.Linear(hidden_size, 1)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        hidden = self.trunk(x)
        probability = torch.sigmoid(self.availability_head(hidden))
        conditional_skr = self.conditional_skr_head(hidden)
        return probability, conditional_skr


class QKDTriHead(QKDDualHead):
    """DualHead-QKD plus a measured-QBER regression head."""

    def __init__(self, input_size: int, hidden_size: int = 12):
        super().__init__(input_size=input_size, hidden_size=hidden_size)
        self.qber_head = nn.Linear(hidden_size, 1)

    def forward(
        self, x: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        hidden = self.trunk(x)
        probability = torch.sigmoid(self.availability_head(hidden))
        conditional_skr = self.conditional_skr_head(hidden)
        qber = self.qber_head(hidden)
        return probability, conditional_skr, qber


class QKDSingleTaskMLP(nn.Module):
    """Same trunk depth as MultiHead, fitted separately to one target."""

    def __init__(self, input_size: int, hidden_size: int = 12, *, probability: bool = False):
        super().__init__()
        self.trunk = _QKDSharedTrunk(input_size, hidden_size)
        self.head = nn.Linear(hidden_size, 1)
        self.probability = bool(probability)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output = self.head(self.trunk(x))
        return torch.sigmoid(output) if self.probability else output


def build_qkd_dominance_pairs(
    available: np.ndarray,
    log_skr_scaled: np.ndarray,
    qber_scaled: np.ndarray,
    strata: np.ndarray,
) -> np.ndarray:
    """Build train-only positive-key dominance constraints within each stratum.

    Every returned row is ``(dominator, dominated, qber_strict, skr_strict)``.
    The last two flags prevent the margin from inventing an ordering for a tied
    objective while still enforcing that the dominating cell is no worse.
    """
    availability = np.asarray(available, dtype=float).reshape(-1)
    skr = np.asarray(log_skr_scaled, dtype=float).reshape(-1)
    qber = np.asarray(qber_scaled, dtype=float).reshape(-1)
    groups = np.asarray(strata).astype(str).reshape(-1)
    lengths = {len(availability), len(skr), len(qber), len(groups)}
    if len(lengths) != 1:
        raise ValueError("dominance inputs must have the same number of cells")
    if not np.isfinite(availability).all() or not np.isfinite(skr).all() or not np.isfinite(qber).all():
        raise ValueError("dominance inputs must be finite")

    pairs: list[tuple[int, int, int, int]] = []
    for stratum in sorted(set(groups)):
        positions = np.flatnonzero((groups == stratum) & (availability >= 0.5))
        for offset, left in enumerate(positions):
            for right in positions[offset + 1:]:
                left_dominates = bool(
                    qber[left] <= qber[right]
                    and skr[left] >= skr[right]
                    and (qber[left] < qber[right] or skr[left] > skr[right])
                )
                right_dominates = bool(
                    qber[right] <= qber[left]
                    and skr[right] >= skr[left]
                    and (qber[right] < qber[left] or skr[right] > skr[left])
                )
                if left_dominates:
                    pairs.append((
                        int(left), int(right),
                        int(qber[left] < qber[right]), int(skr[left] > skr[right]),
                    ))
                elif right_dominates:
                    pairs.append((
                        int(right), int(left),
                        int(qber[right] < qber[left]), int(skr[right] > skr[left]),
                    ))
    return np.asarray(pairs, dtype=np.int64).reshape(-1, 4)


class QKDMultiHeadLoss(nn.Module):
    """Marginal MultiHead losses plus an optional joint dominance penalty."""

    def __init__(
        self,
        *,
        availability_weight: float = 1.0,
        skr_weight: float = 1.0,
        qber_weight: float = 1.0,
        dominance_weight: float = 0.0,
        dominance_margin: float = 0.0,
    ):
        super().__init__()
        self.availability_weight = float(availability_weight)
        self.skr_weight = float(skr_weight)
        self.qber_weight = float(qber_weight)
        self.dominance_weight = float(dominance_weight)
        self.dominance_margin = float(dominance_margin)
        if self.dominance_weight < 0.0 or self.dominance_margin < 0.0:
            raise ValueError("dominance_weight and dominance_margin must be non-negative")
        self.bce = nn.BCELoss(reduction="none")
        self.huber = nn.SmoothL1Loss(reduction="none")

    @staticmethod
    def _weighted_mean(loss: torch.Tensor, weight: torch.Tensor) -> torch.Tensor:
        normalized = weight / weight.sum().clamp(min=1e-12)
        return (loss.reshape(-1) * normalized.reshape(-1)).sum()

    def forward(
        self,
        outputs: tuple[torch.Tensor, ...],
        *,
        available_true: torch.Tensor,
        log_skr_true: torch.Tensor,
        qber_true: torch.Tensor | None,
        sample_weight: torch.Tensor,
        dominance_pairs: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, dict[str, float]]:
        probability, conditional_skr = outputs[:2]
        availability = self._weighted_mean(
            self.bce(probability, available_true), sample_weight
        )
        available_weight = sample_weight * available_true
        skr = self._weighted_mean(
            self.huber(conditional_skr, log_skr_true), available_weight
        )
        total = self.availability_weight * availability + self.skr_weight * skr
        qber = torch.zeros((), dtype=total.dtype, device=total.device)
        if len(outputs) == 3:
            if qber_true is None:
                raise ValueError("TriHead training requires qber_true")
            qber = self._weighted_mean(self.huber(outputs[2], qber_true), sample_weight)
            total = total + self.qber_weight * qber
        dominance = torch.zeros((), dtype=total.dtype, device=total.device)
        if dominance_pairs is not None and dominance_pairs.numel() > 0:
            if len(outputs) != 3:
                raise ValueError("dominance loss requires TriHead QBER and SKR outputs")
            if dominance_pairs.ndim != 2 or dominance_pairs.shape[1] != 4:
                raise ValueError("dominance_pairs must have shape [pairs, 4]")
            dominator = dominance_pairs[:, 0].long()
            dominated = dominance_pairs[:, 1].long()
            qber_strict = dominance_pairs[:, 2].to(total.dtype)
            skr_strict = dominance_pairs[:, 3].to(total.dtype)
            qber_prediction = outputs[2].reshape(-1)
            skr_prediction = conditional_skr.reshape(-1)
            qber_inversion = F.relu(
                qber_prediction[dominator] - qber_prediction[dominated]
                + self.dominance_margin * qber_strict
            )
            skr_inversion = F.relu(
                skr_prediction[dominated] - skr_prediction[dominator]
                + self.dominance_margin * skr_strict
            )
            dominance = (qber_inversion + skr_inversion).mean()
            total = total + self.dominance_weight * dominance
        return total, {
            "availability_loss": float(availability.detach().cpu()),
            "conditional_skr_loss": float(skr.detach().cpu()),
            "qber_loss": float(qber.detach().cpu()),
            "pareto_dominance_loss": float(dominance.detach().cpu()),
        }


@dataclass(frozen=True)
class QKDMultiHeadTrainingSummary:
    epochs: int
    final_loss: float
    component_losses: dict[str, float]
    dominance_pair_count: int = 0


def train_qkd_multihead(
    model: QKDDualHead | QKDTriHead,
    X: np.ndarray,
    available: np.ndarray,
    log_skr_scaled: np.ndarray,
    *,
    qber_scaled: np.ndarray | None = None,
    sample_weight: np.ndarray | None = None,
    seed: int = 42,
    epochs: int = 250,
    learning_rate: float = 3e-3,
    weight_decay: float = 1e-4,
    availability_weight: float = 1.0,
    skr_weight: float = 1.0,
    qber_weight: float = 1.0,
    dominance_pairs: np.ndarray | None = None,
    dominance_weight: float = 0.0,
    dominance_margin: float = 0.0,
) -> tuple[QKDDualHead | QKDTriHead, QKDMultiHeadTrainingSummary]:
    """Train on controlled cells using fixed, predeclared hyperparameters."""
    values = np.asarray(X, dtype=np.float32)
    availability = np.asarray(available, dtype=np.float32).reshape(-1, 1)
    skr = np.asarray(log_skr_scaled, dtype=np.float32).reshape(-1, 1)
    if values.ndim != 2 or len(values) != len(availability) or len(values) < 4:
        raise ValueError("X must be [cells, features] with at least four aligned cells")
    if epochs < 1 or learning_rate <= 0.0:
        raise ValueError("epochs and learning_rate must be positive")
    if sample_weight is None:
        weights = np.ones((len(values), 1), dtype=np.float32)
    else:
        weights = np.asarray(sample_weight, dtype=np.float32).reshape(-1, 1)
    qber = None if qber_scaled is None else np.asarray(
        qber_scaled, dtype=np.float32
    ).reshape(-1, 1)
    if qber is not None and len(qber) != len(values):
        raise ValueError("qber_scaled must align with X")
    pair_values = np.empty((0, 4), dtype=np.int64) if dominance_pairs is None else np.asarray(
        dominance_pairs, dtype=np.int64
    )
    if pair_values.ndim != 2 or pair_values.shape[1] != 4:
        raise ValueError("dominance_pairs must have shape [pairs, 4]")
    if len(pair_values):
        if pair_values[:, :2].min() < 0 or pair_values[:, :2].max() >= len(values):
            raise ValueError("dominance pair indices must refer to X rows")
        if not np.isin(pair_values[:, 2:], (0, 1)).all():
            raise ValueError("dominance strictness flags must be binary")

    torch.manual_seed(int(seed))
    np.random.seed(int(seed))
    for module in model.modules():
        reset = getattr(module, "reset_parameters", None)
        if callable(reset):
            reset()
    X_tensor = torch.from_numpy(values)
    available_tensor = torch.from_numpy(availability)
    skr_tensor = torch.from_numpy(skr)
    weight_tensor = torch.from_numpy(weights)
    qber_tensor = None if qber is None else torch.from_numpy(qber)
    pair_tensor = torch.from_numpy(pair_values)
    criterion = QKDMultiHeadLoss(
        availability_weight=availability_weight,
        skr_weight=skr_weight,
        qber_weight=qber_weight,
        dominance_weight=dominance_weight,
        dominance_margin=dominance_margin,
    )
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=float(learning_rate), weight_decay=float(weight_decay)
    )
    components: dict[str, float] = {}
    final_loss = float("nan")
    model.train()
    for _ in range(int(epochs)):
        optimizer.zero_grad()
        outputs = model(X_tensor)
        loss, components = criterion(
            outputs,
            available_true=available_tensor,
            log_skr_true=skr_tensor,
            qber_true=qber_tensor,
            sample_weight=weight_tensor,
            dominance_pairs=pair_tensor,
        )
        loss.backward()
        optimizer.step()
        final_loss = float(loss.detach())
    model.eval()
    return model, QKDMultiHeadTrainingSummary(
        epochs=int(epochs), final_loss=final_loss, component_losses=components,
        dominance_pair_count=int(len(pair_values)),
    )


def predict_qkd_multihead(
    model: QKDDualHead | QKDTriHead,
    X: np.ndarray,
) -> tuple[np.ndarray, ...]:
    values = torch.from_numpy(np.asarray(X, dtype=np.float32))
    model.eval()
    with torch.no_grad():
        outputs = model(values)
    return tuple(output.cpu().numpy().reshape(-1) for output in outputs)


def train_qkd_single_task(
    model: QKDSingleTaskMLP,
    X: np.ndarray,
    y: np.ndarray,
    *,
    seed: int = 42,
    epochs: int = 250,
    learning_rate: float = 3e-3,
    weight_decay: float = 1e-4,
) -> QKDSingleTaskMLP:
    """Train the capacity-matched independent-head control."""
    values = np.asarray(X, dtype=np.float32)
    targets = np.asarray(y, dtype=np.float32).reshape(-1, 1)
    if values.ndim != 2 or len(values) != len(targets) or len(values) < 4:
        raise ValueError("X must be [cells, features] with at least four aligned targets")
    if epochs < 1 or learning_rate <= 0.0 or weight_decay < 0.0:
        raise ValueError("epochs/learning_rate must be positive and weight_decay non-negative")
    torch.manual_seed(int(seed))
    np.random.seed(int(seed))
    for module in model.modules():
        reset = getattr(module, "reset_parameters", None)
        if callable(reset):
            reset()
    X_tensor = torch.from_numpy(values)
    y_tensor = torch.from_numpy(targets)
    criterion: nn.Module = nn.BCELoss() if model.probability else nn.SmoothL1Loss()
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=float(learning_rate), weight_decay=float(weight_decay)
    )
    model.train()
    for _ in range(int(epochs)):
        optimizer.zero_grad()
        loss = criterion(model(X_tensor), y_tensor)
        loss.backward()
        optimizer.step()
    model.eval()
    return model


def predict_qkd_single_task(model: QKDSingleTaskMLP, X: np.ndarray) -> np.ndarray:
    values = torch.from_numpy(np.asarray(X, dtype=np.float32))
    model.eval()
    with torch.no_grad():
        return model(values).cpu().numpy().reshape(-1)


__all__ = [
    "build_qkd_dominance_pairs",
    "QKDDualHead",
    "QKDMultiHeadLoss",
    "QKDMultiHeadTrainingSummary",
    "QKDSingleTaskMLP",
    "QKDTriHead",
    "predict_qkd_multihead",
    "predict_qkd_single_task",
    "train_qkd_multihead",
    "train_qkd_single_task",
]
