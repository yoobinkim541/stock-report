"""Common recommendation snapshot, calibration, and shadow-first ranking helpers."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real

from ml.recommendation_contracts import canonical_horizon, finite_json_value


_STALE_AFTER_SECONDS = 86_400
_MAX_RISK_PENALTY = 0.5
_MAX_CONCENTRATION_PENALTY = 0.35


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _first(mapping: Mapping, *keys: str) -> object | None:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _bounded(value: object, upper: float) -> float:
    number = _finite_number(value)
    return min(upper, max(0.0, number or 0.0))


def _sample_quality(samples: int | None) -> str:
    if samples is None:
        return "unknown"
    if samples < 20:
        return "low"
    if samples < 50:
        return "medium"
    return "high"


def shrink_probability(wins: int, samples: int, alpha: float = 2.0, beta: float = 2.0) -> float:
    """Return a Beta-posterior success probability for a bounded sample."""
    if samples < 0 or wins < 0 or wins > samples:
        raise ValueError("wins must be between zero and samples")
    if alpha <= 0 or beta <= 0:
        raise ValueError("alpha and beta must be positive")
    return (wins + alpha) / (samples + alpha + beta)


def _uncertainty_bounds(wins: int, samples: int, alpha: float = 2.0, beta: float = 2.0) -> tuple[float, float]:
    posterior_alpha = wins + alpha
    posterior_beta = samples - wins + beta
    total = posterior_alpha + posterior_beta
    mean = posterior_alpha / total
    variance = posterior_alpha * posterior_beta / (total * total * (total + 1))
    spread = 1.96 * math.sqrt(variance)
    return max(0.0, mean - spread), min(1.0, mean + spread)


def _quality_text(candidate: Mapping, source_features: Mapping, missing_fields: list[str], freshness: float | None) -> str:
    issues: list[str] = []
    for value in (_first(candidate, "data_quality"), _first(source_features, "data_quality")):
        if isinstance(value, str) and value.strip():
            issues.extend(part.strip() for part in value.split("|") if part.strip())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            issues.extend(str(part) for part in value if str(part))
    if freshness is not None and freshness > _STALE_AFTER_SECONDS:
        issues.append("stale")
    issues.extend(f"missing_fields:{field}" for field in missing_fields)
    return "|".join(dict.fromkeys(issues)) if issues else "complete"


def _risk_penalty(snapshot: Mapping) -> float:
    explicit = _finite_number(snapshot.get("risk_penalty"))
    if explicit is not None:
        return _bounded(explicit, _MAX_RISK_PENALTY)
    risk = _finite_number(snapshot.get("risk_score"))
    if risk is None:
        return 0.1
    normalized = risk / 100.0 if risk > 1.0 else risk
    return _bounded(normalized * _MAX_RISK_PENALTY, _MAX_RISK_PENALTY)


def _concentration_penalty(snapshot: Mapping, holdings: set[str]) -> float:
    penalty = _bounded(snapshot.get("concentration_penalty"), _MAX_CONCENTRATION_PENALTY)
    ticker = str(snapshot.get("ticker") or "")
    if ticker and ticker in holdings:
        penalty += 0.15
    for key in ("holding_overlap", "sector_overlap", "correlation_overlap"):
        penalty += 0.1 * _bounded(snapshot.get(key), 1.0)
    return min(_MAX_CONCENTRATION_PENALTY, penalty)


def _calibrated_alpha(snapshot: Mapping) -> float | None:
    value = _first(snapshot, "calibrated_alpha", "calibrated_probability", "expected_excess", "raw_score", "score")
    return _finite_number(value)


def build_snapshot(
    surface: str,
    candidate: Mapping,
    *,
    source_features: Mapping | None = None,
    as_of: str | None = None,
    model_version: str | None = None,
) -> dict:
    """Normalize one surface candidate without treating absent evidence as neutral."""
    source = source_features or {}
    decision_ts = _first(candidate, "decision_ts", "snapshot_ts", "timestamp", "date") or as_of
    asof_date = _first(candidate, "asof_date", "as_of", "date")
    if asof_date is None and decision_ts is not None:
        asof_date = str(decision_ts).split("T", 1)[0]

    raw_score = _finite_number(_first(candidate, "raw_score", "score", "total_score"))
    sample_value = _finite_number(_first(candidate, "samples", "sample_size", "n_similar"))
    sample_size = int(sample_value) if sample_value is not None and sample_value >= 0 else None
    wins_value = _finite_number(_first(candidate, "wins", "successes"))
    wins = int(wins_value) if wins_value is not None and wins_value >= 0 else None
    if wins is not None and sample_size is not None and wins > sample_size:
        wins = None

    calibrated_probability = _finite_number(_first(candidate, "calibrated_probability", "shrunk_win_prob"))
    uncertainty_low = uncertainty_high = None
    if wins is not None and sample_size is not None:
        calibrated_probability = shrink_probability(wins, sample_size)
        uncertainty_low, uncertainty_high = _uncertainty_bounds(wins, sample_size)

    freshness = _finite_number(_first(candidate, "freshness_seconds"))
    if freshness is None:
        freshness = _finite_number(_first(source, "freshness_seconds", "median_freshness_seconds"))
    evidence_refs = _first(candidate, "evidence_refs", "evidence_references")
    if isinstance(evidence_refs, Mapping):
        evidence_refs = [dict(evidence_refs)]
    elif isinstance(evidence_refs, Sequence) and not isinstance(evidence_refs, (str, bytes)):
        evidence_refs = list(evidence_refs)
    else:
        evidence_refs = []

    horizon = _first(candidate, "horizon")
    missing_fields = [
        name for name, value in (
            ("id", _first(candidate, "id", "decision_id", "event_id")),
            ("decision_ts", decision_ts),
            ("market", _first(candidate, "market")),
            ("ticker", _first(candidate, "ticker", "symbol")),
            ("horizon", horizon),
            ("raw_score", raw_score),
            ("evidence_refs", evidence_refs or None),
            ("source_coverage", _first(candidate, "source_coverage") or _first(source, "source_coverage", "coverage")),
        ) if value is None
    ]
    if source_features is None:
        missing_fields.append("source_features")

    snapshot = {
        "id": _first(candidate, "id", "decision_id", "event_id"),
        "decision_ts": decision_ts,
        "asof_date": asof_date,
        "surface": str(surface),
        "market": _first(candidate, "market"),
        "ticker": _first(candidate, "ticker", "symbol"),
        "horizon": canonical_horizon(horizon) if horizon is not None else None,
        "raw_score": raw_score,
        "calibrated_probability": calibrated_probability,
        "expected_excess": _finite_number(_first(candidate, "expected_excess", "expected_ret", "expected_ret_20d")),
        "signal": _first(candidate, "signal"),
        "rank": _first(candidate, "rank", "selection_rank"),
        "selection_status": str(_first(candidate, "selection_status") or "shadow"),
        "sample_size": sample_size,
        "sample_quality": _sample_quality(sample_size),
        "uncertainty_low": uncertainty_low,
        "uncertainty_high": uncertainty_high,
        "freshness_seconds": freshness,
        "source_coverage": _first(candidate, "source_coverage") or _first(source, "source_coverage", "coverage"),
        "data_quality": _quality_text(candidate, source, missing_fields, freshness),
        "missing_fields": list(dict.fromkeys(missing_fields)),
        "risk_score": _finite_number(_first(candidate, "risk_score", "risk_pct", "volatility")),
        "concentration_penalty": _bounded(candidate.get("concentration_penalty"), _MAX_CONCENTRATION_PENALTY),
        "total_rank_score": None,
        "model_version": model_version or _first(candidate, "model_version"),
        "parameter_version": _first(candidate, "parameter_version"),
        "feature_version": _first(candidate, "feature_version") or _first(source, "feature_version"),
        "reasons": list(_first(candidate, "reasons") or []),
        "evidence_refs": finite_json_value(evidence_refs),
        "source_features": finite_json_value(source),
        "news_contribution": 0.0,
    }
    alpha = _calibrated_alpha(snapshot)
    if alpha is not None:
        snapshot["total_rank_score"] = alpha - _risk_penalty(snapshot) - snapshot["concentration_penalty"]
    return snapshot


def _partition_rows(rows: Sequence[Mapping], score_key: str, target_key: str) -> tuple[list[tuple[int, Mapping, float, int]], list[tuple[int, Mapping, float, int]]]:
    valid: list[tuple[int, Mapping, float, int]] = []
    has_explicit_partition = False
    for index, row in enumerate(rows):
        score = _finite_number(row.get(score_key))
        target = _finite_number(row.get(target_key))
        if score is None or target is None:
            continue
        valid.append((index, row, score, int(target > 0)))
        split = str(row.get("split") or row.get("partition") or "").lower()
        has_explicit_partition |= split in {"train", "training", "test", "holdout", "validation", "oos"} or isinstance(row.get("is_train"), bool)
    if has_explicit_partition:
        training = []
        held_out = []
        for item in valid:
            row = item[1]
            split = str(row.get("split") or row.get("partition") or "").lower()
            if row.get("is_train") is True or split in {"train", "training"}:
                training.append(item)
            elif row.get("is_train") is False or split in {"test", "holdout", "validation", "oos"}:
                held_out.append(item)
        return training, held_out

    ordered = sorted(valid, key=lambda item: (str(_first(item[1], "decision_ts", "as_of", "asof_date", "date") or ""), item[0]))
    cutoff = max(1, int(len(ordered) * 0.8))
    return ordered[:cutoff], ordered[cutoff:]


def _merge_small_bins(groups: list[list[tuple[int, Mapping, float, int]]], min_bin_size: int) -> list[list[tuple[int, Mapping, float, int]]]:
    while len(groups) > 1:
        small_index = next((index for index, group in enumerate(groups) if len(group) < min_bin_size), None)
        if small_index is None:
            break
        neighbor = 1 if small_index == 0 else small_index - 1
        left, right = sorted((small_index, neighbor))
        groups[left:left + 2] = [groups[left] + groups[left + 1]]
    return groups


def _monotonic_probabilities(groups: Sequence[Sequence[tuple[int, Mapping, float, int]]]) -> list[float]:
    blocks: list[dict[str, float | int]] = []
    for index, group in enumerate(groups):
        positives = sum(item[3] for item in group)
        blocks.append({"start": index, "end": index, "sum": positives, "count": len(group)})
        while len(blocks) > 1 and blocks[-2]["sum"] / blocks[-2]["count"] > blocks[-1]["sum"] / blocks[-1]["count"]:
            right = blocks.pop()
            blocks[-1]["end"] = right["end"]
            blocks[-1]["sum"] += right["sum"]
            blocks[-1]["count"] += right["count"]
    probabilities = [0.0] * len(groups)
    for block in blocks:
        probability = block["sum"] / block["count"]
        for index in range(int(block["start"]), int(block["end"]) + 1):
            probabilities[index] = probability
    return probabilities


def _bin_index(score: float, bins: Sequence[Mapping]) -> int:
    if score <= bins[0]["max_score"]:
        return 0
    for index, bin_ in enumerate(bins[1:], start=1):
        if score <= bin_["max_score"]:
            return index
    return len(bins) - 1


def fit_score_calibrator(
    rows: Sequence[Mapping],
    score_key: str = "raw_score",
    target_key: str = "positive_excess",
    min_bin_size: int = 20,
    min_samples: int = 100,
) -> dict | None:
    """Fit five monotonic score bins and evaluate only the subsequent held-out rows."""
    training, held_out = _partition_rows(rows, score_key, target_key)
    valid_count = len(training) + len(held_out)
    if valid_count < min_samples or not training:
        return None

    ordered_training = sorted(training, key=lambda item: item[2])
    bin_count = max(1, min(5, len(ordered_training) // min_bin_size))
    groups = [
        ordered_training[(index * len(ordered_training)) // bin_count:((index + 1) * len(ordered_training)) // bin_count]
        for index in range(bin_count)
    ]
    groups = _merge_small_bins([group for group in groups if group], min_bin_size)
    probabilities = _monotonic_probabilities(groups)
    bins = [
        {
            "min_score": group[0][2],
            "max_score": group[-1][2],
            "probability": probabilities[index],
            "count": len(group),
        }
        for index, group in enumerate(groups)
    ]

    predictions = [(calibrate_score(score, {"bins": bins}), target) for _, _, score, target in held_out]
    predictions = [(prediction, target) for prediction, target in predictions if prediction is not None]
    if predictions:
        brier = sum((prediction - target) ** 2 for prediction, target in predictions) / len(predictions)
        by_bin: dict[int, list[tuple[float, int]]] = {}
        for prediction, target in predictions:
            by_bin.setdefault(_bin_index(prediction, bins), []).append((prediction, target))
        ece = sum(
            len(values) / len(predictions) * abs(
                sum(prediction for prediction, _ in values) / len(values) - sum(target for _, target in values) / len(values)
            )
            for values in by_bin.values()
        )
        metrics = {"status": "available", "sample_size": len(predictions), "brier": brier, "ece": ece}
    else:
        metrics = {"status": "unavailable", "sample_size": 0, "brier": None, "ece": None}
    return {
        "version": "five-bin-monotonic-v1",
        "score_key": score_key,
        "target_key": target_key,
        "min_bin_size": min_bin_size,
        "training_samples": len(training),
        "held_out_samples": len(held_out),
        "bins": bins,
        "metrics": metrics,
        "brier": metrics["brier"],
        "ece": metrics["ece"],
    }


def calibrate_score(score: float, calibrator: Mapping | None) -> float | None:
    """Map a finite raw score through a fitted calibrator."""
    value = _finite_number(score)
    bins = calibrator.get("bins") if isinstance(calibrator, Mapping) else None
    if value is None or not isinstance(bins, Sequence) or not bins:
        return None
    index = _bin_index(value, bins)
    return _finite_number(bins[index].get("probability"))


def rank_snapshots(
    snapshots: Sequence[Mapping],
    *,
    top_n: int = 5,
    holdings: Sequence[str] = (),
    active: bool = False,
    gate: Mapping | None = None,
) -> list[dict]:
    """Annotate shadow output or, after a passed gate, deterministically rank it."""
    holding_set = {str(ticker) for ticker in holdings}
    gate_passed = bool(gate and gate.get("passed") is True)
    active_ranking = bool(active and gate_passed)
    annotated: list[dict] = []
    for index, original in enumerate(snapshots):
        snapshot = dict(original)
        alpha = _calibrated_alpha(snapshot)
        risk_penalty = _risk_penalty(snapshot)
        concentration_penalty = _concentration_penalty(snapshot, holding_set)
        snapshot["risk_penalty"] = risk_penalty
        snapshot["concentration_penalty"] = concentration_penalty
        snapshot["total_rank_score"] = None if alpha is None else alpha - risk_penalty - concentration_penalty
        source = snapshot.get("source_features")
        news_axis = _finite_number(source.get("news_axis")) if isinstance(source, Mapping) else None
        snapshot["news_contribution"] = news_axis if active_ranking and news_axis is not None else 0.0
        snapshot["ranking_mode"] = "active" if active_ranking else "shadow"
        snapshot["_legacy_index"] = index
        annotated.append(snapshot)

    if active_ranking:
        annotated.sort(key=lambda row: (-(row["total_rank_score"] if row["total_rank_score"] is not None else float("-inf")), row["_legacy_index"]))
        for rank, snapshot in enumerate(annotated, start=1):
            snapshot["rank"] = rank
            snapshot["selection_rank"] = rank
            snapshot["selection_status"] = "selected"
    else:
        for rank, snapshot in enumerate(annotated, start=1):
            snapshot["selection_rank"] = rank
            if snapshot.get("selection_status") not in {"filtered", "invalid"}:
                snapshot["selection_status"] = "shadow"

    result = annotated[:max(0, top_n)]
    for snapshot in result:
        snapshot.pop("_legacy_index", None)
    return result
