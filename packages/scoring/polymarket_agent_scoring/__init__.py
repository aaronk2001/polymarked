"""Smart Score (0-100) computation and red-flag detection for Polymarket wallets."""
from .explain import explain
from .smart_score import RED_FLAG_DEDUCTIONS, WEIGHTS, ScoreReport, compute_smart_score

__all__ = [
    "RED_FLAG_DEDUCTIONS",
    "WEIGHTS",
    "ScoreReport",
    "compute_smart_score",
    "explain",
]
