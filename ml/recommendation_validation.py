"""Fail-closed validation of measured, time-ordered recommendation evidence."""
from collections.abc import Mapping, Sequence
import math
from numbers import Real


def _finite(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def recommendation_gate(metrics: Mapping, baseline: Mapping | None = None) -> dict:
    """All returns/costs/drawdowns are fractions, MDD is a positive loss magnitude.

    Metrics must describe the same purged OOS evaluation and clean-quality subset.
    Baseline measurements must cover that evaluation window, never cached IC alone.
    top_decile_excess is gross benchmark excess; transaction_cost includes fees
    and slippage, so their difference must be strictly positive. Clean-subset
    excess must be measured after costs. Missing evidence is never imputed.
    """
    metrics = metrics if isinstance(metrics, Mapping) else {}
    baseline = baseline if isinstance(baseline, Mapping) else {}
    reasons = []
    checks = {}

    def check(name, passed):
        checks[name] = bool(passed)
        if not passed:
            reasons.append(name)

    required = ("mean_ic", "top_decile_excess", "transaction_cost", "mdd",
                "brier", "ece", "clean_mean_ic", "clean_top_decile_excess")
    for key in required:
        check("finite:" + key, _finite(metrics.get(key)))
    for key in ("mean_ic", "mdd", "brier", "ece"):
        check("finite:baseline." + key, _finite(baseline.get(key)))
    folds = metrics.get("fold_ics")
    n = metrics.get("n_folds")
    folds_valid = (isinstance(folds, (list, tuple)) and len(folds) >= 3
                   and all(_finite(v) and -1 <= v <= 1 for v in folds))
    check("purged_walk_forward", metrics.get("purged") is True and folds_valid
          and _finite(n) and n >= 3 and n == int(n) and n == len(folds))
    if all(checks["finite:" + key] for key in required) and all(
        checks["finite:baseline." + key] for key in ("mean_ic", "mdd", "brier", "ece")
    ):
        check("metric_ranges", all(0 <= obj[key] <= 1 for obj in (metrics, baseline)
                                   for key in ("mdd", "brier", "ece"))
              and metrics["transaction_cost"] >= 0
              and all(-1 <= obj["mean_ic"] <= 1 for obj in (metrics, baseline))
              and -1 <= metrics["clean_mean_ic"] <= 1)
        check("ic_improvement", metrics["mean_ic"] >= baseline["mean_ic"] + .01
              and metrics["mean_ic"] > 0)
        check("fold_mean_consistent", folds_valid and math.isclose(
            metrics["mean_ic"], sum(folds) / len(folds), abs_tol=1e-12))
        check("cost_adjusted_excess", metrics["top_decile_excess"] > metrics["transaction_cost"])
        check("mdd_deterioration", metrics["mdd"] <= baseline["mdd"] + .02)
        check("brier", metrics["brier"] <= baseline["brier"])
        check("ece", metrics["ece"] < baseline["ece"] + .02)
        check("clean_quality_direction", metrics["clean_mean_ic"] > 0
              and metrics["clean_top_decile_excess"] > 0)
    return {"passed": not reasons, "shadow_only": bool(reasons),
            "reasons": reasons, "checks": checks}


def validate_calibration(y_true: Sequence[float], probabilities: Sequence[float]) -> dict:
    """Brier and 10 equal-width probability-bin ECE; never clip/drop invalid rows."""
    try:
        y, p = list(y_true), list(probabilities)
    except TypeError:
        y, p = [], []
    valid = (bool(y) and len(y) == len(p)
             and all(_finite(v) and v in (0, 1) for v in y)
             and all(_finite(v) and 0 <= v <= 1 for v in p))
    if not valid:
        return {"valid": False, "n_samples": len(y), "brier": None,
                "ece": None, "reasons": ["invalid_calibration_samples"]}
    bins = [[] for _ in range(10)]
    for actual, probability in zip(y, p):
        bins[min(int(probability * 10), 9)].append(probability - actual)
    return {"valid": True, "n_samples": len(y),
            "brier": sum((a - b) ** 2 for a, b in zip(y, p)) / len(y),
            "ece": sum(abs(sum(bucket)) for bucket in bins) / len(y), "reasons": []}
