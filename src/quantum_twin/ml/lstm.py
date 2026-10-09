"""
quantum_twin/ml/lstm.py
===========================

Contains the REAL implementation of the point-estimate EdgeLSTM model,
CS_MSELoss, and train_edge_lstm -- migrated here from root-level
models.py in the one-hundred-fifth addendum (TCC item 6), the
twentieth module migrated after the eighty-fifth addendum's
lazy-loading fix. Also contains the REAL implementation of the robust
(mini-batch/validation/early-stopping) trainer (migrated from root
-level models_robust_training.py in the one-hundredth addendum), while
re-exporting the dual-head model and its trainers (from
quantum_twin.ml.dual_head, migrated in the one-hundred-second
addendum) and the probabilistic ensemble model (from
quantum_twin.ml.probabilistic, migrated in the one-hundred-first
addendum).

Root-level models.py and models_robust_training.py are both now thin
re-exports (direction reversed). models.py is kept so its many
pre-existing consumers (spanning nearly every experiment script and
test file in this project) continue to work completely UNCHANGED,
including numerous function-level (deferred) imports across test
files. models_robust_training.py is kept so its eight pre-existing
consumers (causal_chain.py, run_experiment_wdm_vs_privileged.py,
run_lag_analysis.py, tests/test_robust_training.py,
models_probabilistic.py, run_feature_ablation.py,
run_controller_comparison_multiseed.py, run_energy_analysis.py)
continue to work completely UNCHANGED. A sibling shim,
quantum_twin/ml/losses.py (which also imported CS_MSELoss directly
from root models.py), was updated to import from THIS location instead.

A historical note found while auditing consumers: root-level
baselines.py (NOT migrated in this pass) contains a comment on its own
function-level CS_MSELoss import ("local import to avoid a circular
import at module load time") -- verified this addendum: models.py's
OWN imports are limited to torch/torch.nn only (no project-internal
dependencies at all), so this documented concern appears to be a
historical artifact from an earlier architecture, not a live risk in
the current structure. Left unchanged (baselines.py itself was not
touched by this migration), noted here for a future reader's benefit
rather than silently ignored.

Original models_robust_training.py docstring, preserved from the
migrated source:

Direct response to the eleventh addendum's finding: train_edge_lstm's
single-batch, fixed-epoch, no-validation training procedure is the actual
bottleneck behind Predictive's unreliable performance (two independent
seeds collapsed to two DIFFERENT degenerate policies: unconditional
admission at seed 42, unconditional rejection at seed 123).

This module does NOT modify or remove train_edge_lstm (Section 27/28:
preserve existing functionality) -- it adds a parallel, more robust
training procedure and lets the existing one remain available for
backward compatibility / historical comparison.

Three concrete fixes, each targeting a specific failure mode observed:

    1. Mini-batch SGD instead of full-batch gradient descent -- full-batch
       gradient descent on a single fixed loss surface is exactly the
       setup most prone to converging into one of CS_MSELoss's known
       degenerate basins (all-admit or all-reject); mini-batching adds
       enough gradient noise to help escape them.
    2. A held-out temporal validation split + early stopping -- stops
       training at the point of best GENERALIZATION (not just lowest
       training loss), preventing the model from training PAST a good
       decision boundary into a collapsed one.
    3. Learning-rate scheduling (ReduceLROnPlateau) -- reduces step size
       once validation loss plateaus, for finer convergence near a good
       solution instead of overshooting.

A lightweight ensemble (train_edge_lstm_ensemble) is offered as an
ADDITIONAL, complementary safeguard: averaging a few independently
initialized models directly targets the "one bad seed" failure mode by
construction (a systemic collapse would need ALL ensemble members to
collapse the SAME way simultaneously, far less likely than any single one
doing so).

Original models.py docstring, preserved from the migrated source
(Portuguese):

EdgeLSTM adaptado para receber o vetor de estado físico completo (10
variáveis, em vez de 1-2), e CS_MSELoss (mantida como mecanismo de controle
conservador, com o hiperparâmetro de penalidade ajustável).

Arquitetura comparável à versão anterior -- apenas a camada de entrada muda
de tamanho (input_size passa a ser dinâmico, injetado a partir de
QuantumNetworkDataset.input_size).
"""

import copy

import torch
import torch.nn as nn


class EdgeLSTM(nn.Module):
    """
    Rede neural recorrente leve para inferência rápida na borda.

    Arquitetura:
        entrada  -> (batch, seq_len, input_size)  [vetor de estado físico completo]
        LSTM     -> hidden_size compacto, poucas camadas
        saída    -> camada linear + sigmoid, produzindo F_hat(t+1) em [0, 1]
    """

    def __init__(self, input_size: int = 10, hidden_size: int = 16, num_layers: int = 1):
        super().__init__()
        self.input_size = input_size
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
        )
        self.head = nn.Linear(hidden_size, 1)
        self.activation = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        last_hidden = out[:, -1, :]
        pred = self.activation(self.head(last_hidden))
        return pred


class CS_MSELoss(nn.Module):
    """
    Cost-Sensitive Mean Squared Error (CS-MSE), mantida como mecanismo de
    controle conservador do controle de admissão.

    Loss = MSE_ponderado(FP severo, FN moderado) + penalização de descarte excessivo

    lambda_penalty é o hiperparâmetro principal (peso multiplicativo sobre o
    erro quadrático dos Falsos Positivos), consistente com a formulação já
    validada nas versões anteriores do Gêmeo Digital -- preservada aqui como
    ponto de referência, conforme solicitado.
    """

    def __init__(self, threshold: float = 0.65, lambda_penalty: float = 10.0,
                 lambda_fn: float = 2.0, discard_penalty_weight: float = 5.0,
                 max_discard_rate: float = 0.60):
        super().__init__()
        self.threshold = threshold
        self.lambda_penalty = lambda_penalty
        self.lambda_fn = lambda_fn
        self.discard_penalty_weight = discard_penalty_weight
        self.max_discard_rate = max_discard_rate

    def forward(self, y_pred: torch.Tensor, y_true: torch.Tensor) -> torch.Tensor:
        squared_error = (y_pred - y_true) ** 2

        is_false_positive = (y_true < self.threshold) & (y_pred >= self.threshold)
        is_false_negative = (y_true >= self.threshold) & (y_pred < self.threshold)

        weights = torch.ones_like(squared_error)
        weights = torch.where(is_false_positive, torch.full_like(squared_error, self.lambda_penalty), weights)
        weights = torch.where(is_false_negative, torch.full_like(squared_error, self.lambda_fn), weights)

        weighted_mse = (squared_error * weights).mean()

        soft_discard_indicator = torch.sigmoid((self.threshold - y_pred) * 50.0)
        discard_rate = soft_discard_indicator.mean()
        excess_discard = torch.clamp(discard_rate - self.max_discard_rate, min=0.0)
        discard_penalty = self.discard_penalty_weight * (excess_discard ** 2)

        return weighted_mse + discard_penalty


def train_edge_lstm(model: nn.Module, X_train: torch.Tensor, y_train: torch.Tensor,
                     threshold: float = 0.65, lambda_penalty: float = 10.0, lambda_fn: float = 2.0,
                     discard_penalty_weight: float = 5.0, max_discard_rate: float = 0.60,
                     epochs: int = 150, lr: float = 0.012, device: torch.device = None,
                     verbose: bool = False):
    """Rotina de treinamento em batch único (dataset compacto)."""
    if device is not None:
        model = model.to(device)

    criterion = CS_MSELoss(threshold=threshold, lambda_penalty=lambda_penalty, lambda_fn=lambda_fn,
                            discard_penalty_weight=discard_penalty_weight,
                            max_discard_rate=max_discard_rate)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    model.train()
    for epoch in range(epochs):
        optimizer.zero_grad()
        y_pred = model(X_train)
        loss = criterion(y_pred, y_train)
        loss.backward()
        optimizer.step()
        if verbose and (epoch + 1) % 30 == 0:
            with torch.no_grad():
                discard_rate_now = (y_pred < threshold).float().mean().item()
            print(f"    Epoch {epoch + 1:3d}/{epochs} | CS-MSE Loss: {loss.item():.6f} | "
                  f"Taxa de descarte (treino): {discard_rate_now*100:.1f}%")
    return model


from quantum_twin.ml.dual_head import (EdgeLSTMDualHead, DualHeadOrchestratorAdapter,
                                         train_dual_head, train_dual_head_robust)

from quantum_twin.ml.probabilistic import (EdgeLSTMProbabilistic, train_edge_lstm_probabilistic,
                                             EnsembleProbabilisticPredictor, train_ensemble_probabilistic)


def train_edge_lstm_robust(model: nn.Module, X_train_full: torch.Tensor, y_train_full: torch.Tensor,
                            threshold: float = 0.65, lambda_penalty: float = 0.5, lambda_fn: float = 4.0,
                            discard_penalty_weight: float = 25.0, max_discard_rate: float = 0.65,
                            max_epochs: int = 500, lr: float = 0.02, batch_size: int = 64,
                            val_fraction: float = 0.15, patience: int = 25,
                            device: torch.device = None, verbose: bool = False):
    """
    Mini-batch training with a held-out TEMPORAL validation split (the last
    val_fraction of the training data, chronologically -- never shuffled
    across the train/val boundary, to avoid leaking future information into
    early stopping decisions) and early stopping on validation CS_MSELoss.

    Returns (model_with_best_validation_weights_loaded, best_val_loss).
    """
    if device is not None:
        model = model.to(device)

    n = len(X_train_full)
    val_size = max(int(n * val_fraction), 1)
    train_size = n - val_size
    X_tr, y_tr = X_train_full[:train_size], y_train_full[:train_size]
    X_val, y_val = X_train_full[train_size:], y_train_full[train_size:]

    criterion = CS_MSELoss(threshold=threshold, lambda_penalty=lambda_penalty, lambda_fn=lambda_fn,
                            discard_penalty_weight=discard_penalty_weight, max_discard_rate=max_discard_rate)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=8)

    best_val_loss = float("inf")
    best_state = copy.deepcopy(model.state_dict())
    epochs_without_improvement = 0
    n_batches = max(1, train_size // batch_size)

    for epoch in range(max_epochs):
        model.train()
        perm = torch.randperm(train_size)
        for b in range(n_batches):
            idx = perm[b * batch_size:(b + 1) * batch_size]
            if len(idx) == 0:
                continue
            xb, yb = X_tr[idx], y_tr[idx]
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()

        model.eval()
        with torch.no_grad():
            val_loss = criterion(model(X_val), y_val).item()
        scheduler.step(val_loss)

        if val_loss < best_val_loss - 1e-6:
            best_val_loss = val_loss
            best_state = copy.deepcopy(model.state_dict())
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1

        if verbose and (epoch + 1) % 20 == 0:
            print(f"    Epoch {epoch+1:3d}/{max_epochs} | val_loss={val_loss:.6f} | "
                  f"best={best_val_loss:.6f} | patience={epochs_without_improvement}/{patience}")

        if epochs_without_improvement >= patience:
            if verbose:
                print(f"    Early stopping at epoch {epoch+1} (no improvement for {patience} epochs).")
            break

    model.load_state_dict(best_state)
    return model, best_val_loss


class EnsemblePredictor:
    """
    Duck-typed (.eval() / __call__(x) -> tensor) wrapper averaging
    predictions across several independently-trained models. Directly
    targets the "single bad seed collapses" failure mode: a systemic
    collapse now requires ALL members to fail the SAME way at once.
    """

    def __init__(self, models: list, aggregation: str = "mean"):
        self.models = models
        self.aggregation = aggregation

    def eval(self):
        for m in self.models:
            m.eval()
        return self

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        with torch.no_grad():
            preds = torch.stack([m(x) for m in self.models], dim=0)
        if self.aggregation == "median":
            return preds.median(dim=0).values
        return preds.mean(dim=0)


def train_edge_lstm_ensemble(model_factory, X_train_full: torch.Tensor, y_train_full: torch.Tensor,
                              n_models: int = 3, base_seed: int = 1000, **robust_kwargs):
    """
    Trains n_models independently-initialized EdgeLSTM instances (each via
    train_edge_lstm_robust, with a different random seed) and returns an
    EnsemblePredictor averaging their outputs, plus each member's best
    validation loss (for diagnostics).

    model_factory: a zero-argument callable returning a fresh, untrained
                   model instance (e.g. lambda: EdgeLSTM(input_size=..., hidden_size=...)).
    """
    ensemble_models = []
    val_losses = []
    for i in range(n_models):
        torch.manual_seed(base_seed + i)
        model = model_factory()
        trained_model, val_loss = train_edge_lstm_robust(model, X_train_full, y_train_full, **robust_kwargs)
        ensemble_models.append(trained_model)
        val_losses.append(val_loss)
    return EnsemblePredictor(ensemble_models), val_losses


__all__ = ["EdgeLSTM", "train_edge_lstm", "train_edge_lstm_robust", "EnsemblePredictor",
           "train_edge_lstm_ensemble", "EdgeLSTMDualHead", "DualHeadOrchestratorAdapter",
           "train_dual_head", "train_dual_head_robust", "EdgeLSTMProbabilistic",
           "train_edge_lstm_probabilistic", "EnsembleProbabilisticPredictor",
           "train_ensemble_probabilistic"]
