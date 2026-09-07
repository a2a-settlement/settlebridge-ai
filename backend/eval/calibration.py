"""Brier score, reliability bins, and baselines on an identical set."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def brier_score(predictions: Sequence[float], labels: Sequence[int | bool]) -> float:
    """Mean squared error between p(compliant) in [0, 1] and binary labels."""
    if not predictions:
        raise ValueError("brier_score requires at least one prediction")
    if len(predictions) != len(labels):
        raise ValueError("predictions and labels must be the same length")
    total = 0.0
    for p, y in zip(predictions, labels, strict=True):
        total += (float(p) - float(y)) ** 2
    return total / len(predictions)


def naive_brier(labels: Sequence[int | bool]) -> float:
    """Always-predict-base-rate baseline on the same labels."""
    if not labels:
        raise ValueError("naive_brier requires at least one label")
    rate = sum(float(y) for y in labels) / len(labels)
    return brier_score([rate] * len(labels), labels)


def reliability_bins(
    predictions: Sequence[float],
    labels: Sequence[int | bool],
    *,
    n_bins: int = 10,
) -> list[dict[str, Any]]:
    """Observed compliance rate in each predicted-score decile."""
    bins: list[dict[str, Any]] = []
    for i in range(n_bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        pred_i: list[float] = []
        lab_i: list[float] = []
        for p, y in zip(predictions, labels, strict=True):
            p = float(p)
            in_bin = (lo <= p < hi) if i < n_bins - 1 else (lo <= p <= hi)
            if in_bin:
                pred_i.append(p)
                lab_i.append(float(y))
        observed = sum(lab_i) / len(lab_i) if lab_i else None
        mean_p = sum(pred_i) / len(pred_i) if pred_i else None
        bins.append(
            {
                "bin": i,
                "lo": lo,
                "hi": hi,
                "n": len(lab_i),
                "mean_prediction": mean_p,
                "observed_rate": observed,
            }
        )
    return bins


def evaluate_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Score a set of ``{score: 0-100 | p: 0-1, compliant|label}`` rows."""
    preds: list[float] = []
    labels: list[int] = []
    for row in rows:
        if "p" in row:
            preds.append(float(row["p"]))
        else:
            preds.append(float(row["score"]) / 100.0)
        if "label" in row:
            labels.append(int(row["label"]))
        else:
            labels.append(int(bool(row["compliant"])))
    champion_brier = brier_score(preds, labels)
    baseline = naive_brier(labels)
    return {
        "n": len(rows),
        "brier": champion_brier,
        "naive_brier": baseline,
        "base_rate": (sum(labels) / len(labels)) if labels else 0.0,
        "reliability": reliability_bins(preds, labels),
        "beats_naive": champion_brier < baseline,
    }


def compare_to_baselines(
    challenger_brier: float,
    champion_brier: float,
    naive_brier_value: float,
) -> dict[str, Any]:
    """Promotion requires beating both the frozen champion and the naive baseline."""
    from eval.promotion import may_promote

    return {
        "challenger_brier": challenger_brier,
        "champion_brier": champion_brier,
        "naive_brier": naive_brier_value,
        "beats_champion": challenger_brier < champion_brier,
        "beats_naive": challenger_brier < naive_brier_value,
        "may_promote": may_promote(challenger_brier, champion_brier, naive_brier_value),
    }
