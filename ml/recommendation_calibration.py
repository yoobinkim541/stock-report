"""Bounded, point-in-time calibration artifacts; no ranking or trading side effects.

Timestamps are normalized to UTC. Legacy date-only labels are conservatively
available at the end of that UTC day; naive intraday timestamps are rejected.
An outcome's explicit horizon (not a decision's preferred horizon) labels it.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from numbers import Real
from pathlib import Path

from ml.recommendation_contracts import canonical_horizon, dedupe_canonical_outcomes, finite_json_value
from ml.recommendation_engine import fit_score_calibrator
from safe_io import atomic_write_json


ARTIFACT_VERSION = "recommendation-calibration-v1"
SNAPSHOT_VERSION = "recommendation-ledger-snapshot-v1"
DEFAULT_DIR = Path("~/reports/ml-cache/recommendation-calibration").expanduser()
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
_METRICS = ("fwd_excess", "excess_ret", "fwd_ret", "stock_ret", "benchmark_ret",
            "net_ret", "gross_ret", "fwd_mdd", "idx_fwd_mdd", "mfe", "mae", "r_multiple")


def _finite(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _timestamp(value, *, end_of_day=False):
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, date):
        stamp = datetime.combine(value, time.max if end_of_day else time.min, timezone.utc)
    elif isinstance(value, str):
        try:
            if len(value) == 10:
                return _timestamp(date.fromisoformat(value), end_of_day=end_of_day)
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp.astimezone(timezone.utc)


def _asof(value):
    stamp = datetime.now(timezone.utc) if value is None else _timestamp(value, end_of_day=True)
    if stamp is None:
        raise ValueError("as_of must be an ISO date or timezone-aware timestamp")
    return stamp


def _scale(value):
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or not all(_finite(v) for v in value) or value[0] >= value[1]):
        raise ValueError("score_range must contain two finite increasing numbers")
    return tuple(float(v) for v in value)


def _metadata(row, key):
    direct = row.get(key)
    metadata = row.get("metadata")
    nested = metadata.get(key) if isinstance(metadata, Mapping) else None
    if direct is not None and nested is not None and direct != nested:
        raise ValueError("conflicting_metadata:" + key)
    value = direct if direct is not None else nested
    return value if isinstance(value, str) and value.strip() else None


def _base_id(row, *, decision=False):
    value = row.get("base_decision_id") or row.get("id" if decision else "decision_id")
    return str(value).rsplit(":h", 1)[0] if value else ""


def _outcome_identity_reason(row):
    decision_id = row.get("decision_id")
    if not isinstance(decision_id, str) or not decision_id.strip():
        return "invalid_outcome_decision_id"
    if "base_decision_id" in row:
        base_id = row["base_decision_id"]
        if not isinstance(base_id, str) or not base_id.strip():
            return "invalid_outcome_base_id"
        if base_id != decision_id.rsplit(":h", 1)[0]:
            return "conflicting_outcome_identity"
    return None


def _outcome_version_reason(row, scope):
    metadata = row.get("metadata")
    if metadata is not None and not isinstance(metadata, Mapping):
        return "invalid_outcome_metadata"
    metadata = metadata if metadata is not None else {}
    for key in ("model_version", "parameter_version"):
        values = [source[key] for source in (row, metadata) if key in source]
        if any(value is not None and not isinstance(value, str) for value in values):
            return "invalid_outcome_version:" + key
        if len(values) == 2 and values[0] != values[1]:
            return "conflicting_outcome_version:" + key
        if values and values[0] != scope[key]:
            return "outcome_version_mismatch:" + key
    return None


def _horizon(row):
    raw = row.get("horizon")
    if raw is None:
        suffix = str(row.get("decision_id") or "").rsplit(":h", 1)
        raw = suffix[1] if len(suffix) == 2 else ""
    return canonical_horizon(raw) if raw not in (None, "") else ""


def _decision_horizon(row):
    suffix = str(row.get("id") or row.get("decision_id") or "").rsplit(":h", 1)
    return canonical_horizon(suffix[1]) if len(suffix) == 2 else None


def _json(value):
    return json.dumps(finite_json_value(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _artifact_path(scope, base_dir):
    key = hashlib.sha256(_json(scope).encode()).hexdigest()
    return (Path(base_dir).expanduser() if base_dir is not None else DEFAULT_DIR) / f"{key}.json"


def _bounded(rows, max_rows):
    result = []
    for row in rows:
        if len(result) >= max_rows:
            raise ValueError("max_rows exceeded; refusing a partial source snapshot")
        result.append(row)
    return result


def _valid_bins(calibrator, score_range):
    if not isinstance(calibrator, Mapping):
        return False
    bins = calibrator.get("bins")
    if not isinstance(bins, list) or not bins:
        return False
    previous_max, previous_probability = score_range[0], 0.
    for item in bins:
        if not isinstance(item, Mapping):
            return False
        low, high, probability = (item.get(k) for k in ("min_score", "max_score", "probability"))
        if (not all(_finite(v) for v in (low, high, probability))
                or not score_range[0] <= low <= high <= score_range[1]
                or low < previous_max or not previous_probability <= probability <= 1):
            return False
        previous_max, previous_probability = high, probability
    return True


def _valid_metrics(metrics, held_out_samples):
    return (isinstance(metrics, Mapping)
            and metrics.get("valid") is True
            and isinstance(metrics.get("n_samples"), int)
            and not isinstance(metrics["n_samples"], bool)
            and metrics["n_samples"] == held_out_samples
            and metrics.get("reasons") == []
            and all(_finite(metrics.get(k)) and 0 <= metrics[k] <= 1 for k in ("brier", "ece")))


def _baseline_passes(baseline, validation):
    return (validation["brier"] <= baseline["brier"]
            and validation["ece"] < baseline["ece"] + .02)


def _known_version(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().lower() not in {
        "unknown", "none", "null", "n/a", "unversioned"}


def _fit_evidence_matches(calibrator, scale, held_out_samples):
    try:
        return (_scale(calibrator.get("score_range")) == scale
                and all(_valid_metrics(calibrator.get(k), held_out_samples)
                        for k in ("baseline_metrics", "validation_metrics"))
                and _baseline_passes(calibrator["baseline_metrics"], calibrator["validation_metrics"]))
    except (ValueError, TypeError, AttributeError):
        return False


def train_artifacts(
    decisions: Iterable[Mapping], outcomes: Iterable[Mapping], *,
    surface: str = "entry_signals", as_of=None, score_range=None, base_dir=None,
    min_samples: int = 100, min_bin_size: int = 20, min_holdout_samples: int = 20,
    holdout_fraction: float = .2, max_rows: int = 100_000, max_scopes: int = 100,
) -> list[dict]:
    """Persist one diagnostic artifact per observed scope and return those artifacts.

    min_samples counts purged training plus holdout rows, matching the fit API.
    Limits fail before writes, rather than silently training a partial snapshot.
    A missing fit acceptance result is diagnostic-only, never accepted.
    """
    if surface != "entry_signals" and score_range is None:
        raise ValueError("non-entry surfaces require an explicit score_range")
    scale = _scale((0., 1.) if score_range is None else score_range)
    thresholds = (min_samples, min_bin_size, min_holdout_samples, max_rows, max_scopes)
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in thresholds):
        raise ValueError("sample and bound thresholds must be positive integers")
    if not _finite(holdout_fraction) or not 0 < holdout_fraction < 1:
        raise ValueError("holdout_fraction must be between zero and one")
    if not isinstance(surface, str) or not surface:
        raise ValueError("surface is required")
    cutoff = _asof(as_of)
    decisions = _bounded(decisions, max_rows)
    outcomes = _bounded(outcomes, max_rows)
    snapshot = {"version": SNAPSHOT_VERSION,
                "digest": hashlib.sha256(_json({"decisions": decisions, "outcomes": outcomes}).encode()).hexdigest(),
                "decision_count": len(decisions), "outcome_count": len(outcomes)}
    indexed = defaultdict(list)
    global_rejections = Counter()
    for decision in decisions:
        if not isinstance(decision, Mapping) or not _base_id(decision, decision=True):
            global_rejections["invalid_decision_id"] += 1
            continue
        indexed[_base_id(decision, decision=True)].append(decision)
    scopes = {}
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            global_rejections["invalid_outcome"] += 1
            continue
        identity_reason = _outcome_identity_reason(outcome)
        if identity_reason:
            global_rejections[identity_reason] += 1
            continue
        horizon = _horizon(outcome)
        candidates = [d for d in indexed.get(_base_id(outcome), [])
                      if _decision_horizon(d) in (None, horizon)]
        if not candidates or not horizon:
            global_rejections["unmatched_decision_or_horizon"] += 1
            continue
        decision = candidates[0]
        try:
            scope = {"surface": surface, "horizon": horizon, "market": _metadata(decision, "market"),
                     "model_version": _metadata(decision, "model_version"),
                     "parameter_version": _metadata(decision, "parameter_version")}
        except ValueError as exc:
            global_rejections[str(exc)] += 1
            continue
        key = _json(scope)
        if key not in scopes:
            if len(scopes) >= max_scopes:
                raise ValueError("max_scopes exceeded; refusing a partial refresh")
            scopes[key] = {"scope": scope, "eligible": [], "rejections": Counter(),
                           "decisions": set(), "conflicts": set(), "outcomes": 0,
                           "duplicate_decisions": {}}
        group = scopes[key]
        group["outcomes"] += 1
        base_id = _base_id(outcome)
        group["decisions"].add(base_id)
        group["duplicate_decisions"][base_id] = max(0, len(candidates) - 1)
        reject = group["rejections"]
        encoded_horizon = str(outcome.get("decision_id") or "").rsplit(":h", 1)
        if len(encoded_horizon) == 2 and canonical_horizon(encoded_horizon[1]) != horizon:
            reject["conflicting_outcome_horizon"] += 1
            continue
        if any(_json(candidate) != _json(decision) for candidate in candidates[1:]):
            if base_id not in group["conflicts"]:
                reject["conflicting_decision"] += 1
                group["conflicts"].add(base_id)
            continue
        if decision.get("surface") not in (None, surface) or outcome.get("surface") not in (None, surface):
            reject["surface_mismatch"] += 1
            continue
        if not scope["market"] or outcome.get("market") not in (None, scope["market"]):
            reject["missing_or_mismatched_market"] += 1
            continue
        version_reason = _outcome_version_reason(outcome, scope)
        if version_reason:
            reject[version_reason] += 1
            continue
        stamp_value = next((decision[k] for k in ("decision_ts", "snapshot_ts", "date", "asof_date")
                            if decision.get(k) is not None), None)
        stamp = _timestamp(stamp_value)
        if stamp is None or stamp > cutoff:
            reject["invalid_or_future_decision_timestamp"] += 1
            continue
        if str(outcome.get("status") or "").lower() != "matured":
            reject["outcome_not_matured"] += 1
            continue
        times = [outcome[k] for k in ("available_at", "outcome_available_at", "matured_at", "exit_date")
                 if outcome.get(k) is not None]
        if not times:
            reject["missing_outcome_timestamp"] += 1
            continue
        parsed = [_timestamp(v, end_of_day=True) for v in times]
        if any(v is None for v in parsed):
            reject["invalid_outcome_timestamp"] += 1
            continue
        available = max(parsed)
        if available > cutoff:
            reject["future_outcome_availability"] += 1
            continue
        if available < stamp:
            reject["outcome_precedes_decision"] += 1
            continue
        excess = outcome.get("fwd_excess", outcome.get("excess_ret"))
        if not _finite(excess) or any(k in outcome and not _finite(outcome[k]) for k in _METRICS):
            reject["invalid_outcome_metric"] += 1
            continue
        score = decision.get("raw_score")
        if not _finite(score) or not scale[0] <= score <= scale[1]:
            reject["invalid_or_out_of_range_score"] += 1
            continue
        try:
            score_version = _metadata(decision, "score_version")
        except ValueError as exc:
            reject[str(exc)] += 1
            continue
        group["eligible"].append({**outcome, "base_decision_id": base_id,
            "_calibration_row": {"decision_id": base_id, "decision_ts": stamp.isoformat(),
                "outcome_available_at": available.isoformat(), "raw_score": float(score),
                "positive_excess": int(excess > 0), "score_version": score_version}})

    artifacts = []
    for key in sorted(scopes):
        group = scopes[key]
        eligible = group["eligible"]
        deduped = dedupe_canonical_outcomes(eligible)
        rows = sorted((o["_calibration_row"] for o in deduped),
                      key=lambda r: (r["decision_ts"], r["decision_id"]))
        days = sorted({r["decision_ts"][:10] for r in rows})
        # The boundary is a whole decision day, never an arbitrary row index.
        desired_holdout = max(min_holdout_samples, math.ceil(len(rows) * holdout_fraction))
        boundary = rows[max(0, len(rows) - desired_holdout)]["decision_ts"][:10] if len(days) >= 2 else None
        held_out = [r for r in rows if boundary is not None and r["decision_ts"][:10] >= boundary]
        training = [r for r in rows if boundary is None or r["decision_ts"][:10] < boundary]
        first_holdout = min((r["decision_ts"] for r in held_out), default=None)
        purged = [r for r in training if first_holdout is not None
                  and r["outcome_available_at"] >= first_holdout]
        training = [r for r in training if first_holdout is None or r["outcome_available_at"] < first_holdout]
        fit_rows = [{**r, "split": "train"} for r in training] + [{**r, "split": "holdout"} for r in held_out]
        scope = group["scope"]
        versions = sorted({v for r in training + held_out for v in
                           (scope["model_version"], scope["parameter_version"], r["score_version"])
                           if _known_version(v)})
        reasons = []
        if not versions:
            reasons.append("unknown_source_score_version")
        if not any(_known_version(scope[k]) for k in ("model_version", "parameter_version")):
            reasons.append("unknown_lookup_score_version")
        if len(fit_rows) < min_samples:
            reasons.append("insufficient_samples")
        if len(training) < min_bin_size:
            reasons.append("insufficient_training_samples")
        if len(held_out) < min_holdout_samples:
            reasons.append("insufficient_holdout_samples")
        calibrator = None
        if not any(r.startswith("insufficient_") for r in reasons):
            calibrator = fit_score_calibrator(
                fit_rows, min_samples=min_samples, min_bin_size=min_bin_size,
                score_range=scale, min_holdout_samples=min_holdout_samples)
            acceptance = calibrator.get("acceptance") if isinstance(calibrator, Mapping) else None
            if (not isinstance(acceptance, Mapping) or not isinstance(acceptance.get("accepted"), bool)
                    or not isinstance(acceptance.get("reasons"), list)
                    or not all(isinstance(r, str) for r in acceptance["reasons"])):
                reasons.append("missing_fit_acceptance")
            elif acceptance["accepted"] is True and acceptance["reasons"]:
                reasons.append("inconsistent_fit_acceptance")
            elif acceptance["accepted"] is not True:
                fit_reasons = acceptance.get("reasons")
                reasons.extend(fit_reasons if isinstance(fit_reasons, list) and all(isinstance(r, str) for r in fit_reasons)
                               and fit_reasons else ["fit_rejected"])
            if not _valid_bins(calibrator, scale):
                reasons.append("invalid_calibrator")
            if isinstance(acceptance, Mapping) and acceptance.get("accepted") is True:
                if not _fit_evidence_matches(calibrator, scale, len(held_out)):
                    reasons.append("invalid_fit_evidence")
        counts = {"decisions": len(group["decisions"]), "outcomes": group["outcomes"],
                  "joined": len(rows), "training": len(training), "holdout": len(held_out),
                  "purged": len(purged), "duplicate_outcomes": len(eligible) - len(deduped),
                  "duplicate_decisions": sum(group["duplicate_decisions"].values())}
        artifact = {"version": ARTIFACT_VERSION, "trained_at": datetime.now(timezone.utc).isoformat(),
                    "as_of": cutoff.isoformat(), "validation_end_date": max((r["decision_ts"] for r in held_out), default=None),
                    "scope": scope, "score_range": list(scale), "calibrator": finite_json_value(calibrator),
                    "source_snapshot": snapshot, "source_score_versions": versions,
                    "counts": counts, "purge_counts": {"outcome_unavailable_at_holdout": len(purged)},
                    "rejections": dict(group["rejections"]), "source_rejections": dict(global_rejections),
                    "acceptance": {"accepted": not reasons and calibrator is not None, "reasons": reasons},
                    "baseline_metrics": finite_json_value(calibrator.get("baseline_metrics")) if calibrator else None,
                    "validation_metrics": finite_json_value(calibrator.get("validation_metrics")) if calibrator else None,
                    "thresholds": {"min_samples": min_samples, "min_bin_size": min_bin_size,
                                   "min_holdout_samples": min_holdout_samples, "holdout_fraction": holdout_fraction}}
        atomic_write_json(str(_artifact_path(scope, base_dir)), artifact)
        artifacts.append(artifact)
    return artifacts


def load_accepted_calibrator(surface, horizon, market, model_version, parameter_version,
                             as_of, score_range=(0, 1), base_dir=None) -> dict | None:
    """Return the full accepted artifact (nested calibrator), or fail closed.

    The artifact must match the exact scope/scale and be non-future, <=90d old.
    """
    try:
        cutoff = _asof(as_of)
        scale = _scale(score_range)
        scope = {"surface": surface, "horizon": canonical_horizon(horizon), "market": market,
                 "model_version": model_version, "parameter_version": parameter_version}
        lookup_versions = (model_version, parameter_version)
        if (any(v is not None and not isinstance(v, str) for v in lookup_versions)
                or not any(_known_version(v) for v in lookup_versions)):
            return None
        path = _artifact_path(scope, base_dir)
        with path.open("rb") as stream:
            raw = stream.read(MAX_ARTIFACT_BYTES + 1)
        if len(raw) > MAX_ARTIFACT_BYTES:
            return None
        artifact = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if (not isinstance(artifact, dict) or artifact.get("version") != ARTIFACT_VERSION
                or artifact.get("scope") != scope or _scale(artifact.get("score_range")) != scale):
            return None
        acceptance = artifact.get("acceptance")
        if not isinstance(acceptance, dict) or acceptance.get("accepted") is not True or acceptance.get("reasons") != []:
            return None
        versions = artifact.get("source_score_versions")
        if not isinstance(versions, list) or not versions or not all(_known_version(v) for v in versions):
            return None
        if any(_known_version(v) and v not in versions for v in (model_version, parameter_version)):
            return None
        timestamps = [_timestamp(artifact.get(k), end_of_day=True)
                      for k in ("trained_at", "validation_end_date", "as_of")]
        if any(t is None or t > cutoff or cutoff - t > timedelta(days=90) for t in timestamps):
            return None
        snapshot = artifact.get("source_snapshot")
        if (not isinstance(snapshot, dict) or snapshot.get("version") != SNAPSHOT_VERSION
                or not isinstance(snapshot.get("digest"), str) or len(snapshot["digest"]) != 64
                or any(c not in "0123456789abcdef" for c in snapshot["digest"])):
            return None
        calibrator = artifact.get("calibrator")
        if not _valid_bins(calibrator, scale) or _scale(calibrator.get("score_range")) != scale:
            return None
        fit_acceptance = calibrator.get("acceptance")
        if (not isinstance(fit_acceptance, dict) or fit_acceptance.get("accepted") is not True
                or fit_acceptance.get("reasons") != []):
            return None
        counts, thresholds = artifact.get("counts"), artifact.get("thresholds")
        if not isinstance(counts, dict) or not isinstance(thresholds, dict):
            return None
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in counts.values()):
            return None
        for count_key, threshold_key in (("training", "min_bin_size"), ("holdout", "min_holdout_samples")):
            threshold = thresholds.get(threshold_key)
            if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1 or counts[count_key] < threshold:
                return None
        if (counts["joined"] != counts["training"] + counts["holdout"] + counts["purged"]
                or counts["training"] + counts["holdout"] < thresholds["min_samples"]):
            return None
        for key in ("baseline_metrics", "validation_metrics"):
            if not _valid_metrics(artifact.get(key), counts["holdout"]) or artifact[key] != calibrator.get(key):
                return None
        if not _baseline_passes(artifact["baseline_metrics"], artifact["validation_metrics"]):
            return None
        return artifact
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return None
