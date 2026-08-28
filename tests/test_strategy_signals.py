from __future__ import annotations

import pandas as pd
import pytest

from ml.strategy_studio import (
    SignalPanel,
    StrategySpec,
    build_signal_panel,
    combine_signal_panels,
    compile_strategy,
    run_strategy_backtest,
)
from ml.strategy_studio.registry import register_model
from ml.data_pipeline import normalize_data_snapshot


def test_momentum_provider_uses_only_prior_bars_for_score():
    prices = pd.DataFrame(
        {"AAPL": [100, 101, 102, 99, 103]},
        index=pd.date_range("2026-01-01", periods=5),
    )
    spec = StrategySpec.from_dict({
        "name": "momentum", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "factor", "plugin": "momentum", "lookback": 2},
    })

    compiled = compile_strategy(spec, prices)
    panel = build_signal_panel(spec, compiled)

    assert panel.score.loc[pd.Timestamp("2026-01-03"), "AAPL"] == pytest.approx(0.02)
    assert pd.isna(panel.score.iloc[0, 0])


def test_rule_provider_converts_entry_exit_and_trim_to_scores():
    prices = pd.DataFrame(
        {"AAPL": [100, 101, 99]},
        index=pd.date_range("2026-01-01", periods=3),
    )
    spec = StrategySpec.from_dict({
        "name": "rule", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "rules": {
            "entry": [{"field": "close", "op": ">", "value": 100, "label": "entry"}],
            "exit": [{"field": "close", "op": "<", "value": 100, "label": "exit"}],
            "trim": [{"field": "close", "op": "==", "value": 101, "label": "trim"}],
        },
        "signal": {"type": "rule"},
    })

    panel = build_signal_panel(spec, compile_strategy(spec, prices))

    assert list(panel.score["AAPL"]) == [0.0, 0.0, -1.0]
    assert panel.reason.loc[pd.Timestamp("2026-01-02"), "AAPL"] == "trim"


def test_volatility_provider_marks_zero_volatility_invalid():
    prices = pd.DataFrame(
        {"AAPL": [100, 100, 100, 100]},
        index=pd.date_range("2026-01-01", periods=4),
    )
    spec = StrategySpec.from_dict({
        "name": "volatility", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "factor", "plugin": "volatility", "lookback": 2},
    })

    panel = build_signal_panel(spec, compile_strategy(spec, prices))

    assert panel.score["AAPL"].isna().all()
    assert any("zero volatility" in diagnostic for diagnostic in panel.diagnostics)


def test_cross_sectional_rank_ranks_active_universe_at_each_timestamp():
    prices = pd.DataFrame(
        {"A": [100, 102], "B": [100, 99], "C": [100, 101]},
        index=pd.date_range("2026-01-01", periods=2),
    )
    spec = StrategySpec.from_dict({
        "name": "rank", "market": "multi",
        "universe": {"type": "list", "symbols": ["A", "B", "C"]},
        "signal": {
            "type": "factor", "plugin": "cross_sectional_rank",
            "source": "momentum", "lookback": 1,
        },
    })

    panel = build_signal_panel(spec, compile_strategy(spec, prices))

    assert panel.score.loc[pd.Timestamp("2026-01-02"), "A"] == pytest.approx(1.0)
    assert panel.score.loc[pd.Timestamp("2026-01-02"), "B"] == pytest.approx(0.0)
    assert panel.score.loc[pd.Timestamp("2026-01-02"), "C"] == pytest.approx(0.5)


def test_ensemble_confidence_is_weighted_and_invalid_member_is_reported():
    panel = combine_signal_panels([
        SignalPanel.from_score("rule", pd.DataFrame({"AAPL": [1.0]}), confidence=0.8),
        SignalPanel.from_score("model", pd.DataFrame({"AAPL": [-0.5]}), confidence=0.6),
    ], weights=[0.75, 0.25])

    assert panel.score.iloc[0, 0] == pytest.approx(0.625)
    assert panel.confidence.iloc[0, 0] == pytest.approx(0.75 * 0.8 + 0.25 * 0.6)

    invalid = SignalPanel.invalid("missing", "provider returned an invalid signal")
    combined = combine_signal_panels([panel, invalid], weights=[0.5, 0.5])
    assert any("invalid signal" in diagnostic for diagnostic in combined.diagnostics)


def test_model_provider_rejects_missing_prediction_metadata_without_fabricating_score():
    class BareModel:
        def predict(self, features):
            return [0.4] * len(features)

    register_model("bare-model-task-2", BareModel(), {"model_id": "bare-model-task-2"})
    prices = pd.DataFrame({"AAPL": [100, 101]}, index=pd.date_range("2026-01-01", periods=2))
    spec = StrategySpec.from_dict({
        "name": "model", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "model", "ref": "bare-model-task-2"},
    })

    panel = build_signal_panel(spec, compile_strategy(spec, prices))

    assert panel.score.isna().all().all()
    assert any("prediction metadata" in diagnostic for diagnostic in panel.diagnostics)


def test_model_provider_broadcasts_registered_confidence_and_preserves_versions():
    class FixedModel:
        feature_names_ = ["close"]

        def predict(self, features):
            return [0.25] * len(features)

    register_model(
        "fixed-model-task-2",
        FixedModel(),
        {
            "model_id": "fixed-model-task-2",
            "feature_version": "features-2026-08-28",
            "model_version": "fixed-model-v1",
            "as_of": "2026-01-01T00:00:00+00:00",
            "confidence": 0.7,
            "feature_names": ["close"],
        },
    )
    prices = pd.DataFrame({"AAPL": [100, 101]}, index=pd.date_range("2026-01-01", periods=2))
    spec = StrategySpec.from_dict({
        "name": "model", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "model", "ref": "fixed-model-task-2"},
    })

    panel = build_signal_panel(spec, compile_strategy(spec, prices))

    assert panel.score["AAPL"].tolist() == [0.25, 0.25]
    assert panel.confidence["AAPL"].tolist() == [0.7, 0.7]
    assert panel.feature_version["AAPL"].iloc[0] == "features-2026-08-28"
    assert panel.model_version["AAPL"].iloc[0] == "fixed-model-v1"


def test_model_provider_consumes_plural_snapshots_and_reports_stale_freshness():
    class FixedModel:
        feature_names_ = ["close"]

        def predict(self, features):
            return [0.25] * len(features)

    snapshots = {}
    for symbol, received_at in (("AAPL", "2026-01-02T00:00:00Z"), ("MSFT", "2026-01-02T00:00:00Z")):
        frame = pd.DataFrame(
            {"close": [100.0, 101.0]},
            index=pd.date_range("2026-01-01", periods=2, tz="UTC"),
        )
        snapshots[symbol] = normalize_data_snapshot(
            frame,
            symbol=symbol,
            source="test-source",
            timeframe="1d",
            session="regular",
            adjustment="raw",
            received_at=received_at,
            available_at=received_at,
        ).to_dict()

    prices = pd.DataFrame(
        {"AAPL": [100.0, 101.0]},
        index=pd.date_range("2026-01-01", periods=2, tz="UTC"),
    )
    prices.attrs["data_snapshots"] = snapshots
    register_model(
        "plural-snapshot-model",
        FixedModel(),
        {
            "model_id": "plural-snapshot-model",
            "feature_version": "features-v1",
            "model_version": "model-v1",
            "as_of": "2025-12-31T00:00:00Z",
            "confidence": 0.7,
            "feature_names": ["close"],
            "evaluation_at": "2026-01-03T00:00:00Z",
            "max_data_age_seconds": 60,
        },
    )
    spec = StrategySpec.from_dict({
        "name": "model", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "model", "ref": "plural-snapshot-model"},
    })

    panel = build_signal_panel(spec, compile_strategy(spec, prices))

    assert panel.confidence["AAPL"].tolist() == [0.0, 0.0]
    assert any("data_stale" in diagnostic for diagnostic in panel.diagnostics)


def test_model_provider_rejects_a_feature_schema_mismatch_with_diagnostic():
    class FixedModel:
        def predict(self, features):
            return [0.25] * len(features)

    register_model(
        "schema-model-task-2",
        FixedModel(),
            {
                "model_id": "schema-model-task-2",
                "feature_version": "features-2026-08-28",
                "model_version": "schema-model-v1",
                "as_of": "2026-01-01T00:00:00+00:00",
            "confidence": 0.7,
            "feature_names": "different_feature",
        },
    )
    prices = pd.DataFrame({"AAPL": [100, 101]}, index=pd.date_range("2026-01-01", periods=2))
    spec = StrategySpec.from_dict({
        "name": "model", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "model", "ref": "schema-model-task-2"},
    })

    panel = build_signal_panel(spec, compile_strategy(spec, prices))

    assert not panel.has_valid_scores
    assert any("feature columns do not match" in diagnostic for diagnostic in panel.diagnostics)


def test_model_provider_preserves_adapter_model_version_and_rejects_mismatch():
    class VersionedModel:
        def __init__(self, model_id, model_version="adapter-v2"):
            self.model_id = model_id
            self.model_version = model_version

        def predict_with_metadata(self, features):
            return {
                "model_id": self.model_id,
                "feature_version": "features-2026-08-28",
                "model_version": self.model_version,
                "predictions": [0.3] * len(features),
                "confidence": [0.8] * len(features),
                "as_of": "2026-01-01T00:00:00+00:00",
            }

    register_model(
        "versioned-model-task-2",
        VersionedModel("versioned-model-task-2"),
        {
            "model_id": "versioned-model-task-2",
            "feature_version": "features-2026-08-28",
            "model_version": "adapter-v2",
            "as_of": "2026-01-01T00:00:00+00:00",
            "confidence": 0.8,
        },
    )
    prices = pd.DataFrame({"AAPL": [100, 101]}, index=pd.date_range("2026-01-01", periods=2))
    spec = StrategySpec.from_dict({
        "name": "model", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "model", "ref": "versioned-model-task-2"},
    })

    panel = build_signal_panel(spec, compile_strategy(spec, prices))

    assert panel.model_version["AAPL"].tolist() == ["adapter-v2", "adapter-v2"]

    register_model(
        "mismatched-model-task-2",
        VersionedModel("mismatched-model-task-2"),
        {
            "model_id": "mismatched-model-task-2",
            "feature_version": "features-2026-08-28",
            "model_version": "registered-v1",
            "as_of": "2026-01-01T00:00:00+00:00",
            "confidence": 0.8,
        },
    )
    mismatched_spec = replace_signal_ref(spec, "mismatched-model-task-2")
    mismatched = build_signal_panel(mismatched_spec, compile_strategy(mismatched_spec, prices))

    assert not mismatched.has_valid_scores
    assert any("model_version mismatch" in diagnostic for diagnostic in mismatched.diagnostics)


def test_model_provider_invalidates_posthoc_rows_and_nan_confidence():
    class FixedModel:
        def predict(self, features):
            return [0.25] * len(features)

    register_model(
        "invalid-rows-model-task-2",
        FixedModel(),
        {
            "model_id": "invalid-rows-model-task-2",
            "feature_version": "features-2026-08-28",
            "model_version": "invalid-rows-v1",
            "as_of": "2026-01-02T00:00:00+00:00",
            "confidence": [0.7, float("nan")],
        },
    )
    prices = pd.DataFrame({"AAPL": [100, 101]}, index=pd.date_range("2026-01-01", periods=2))
    spec = StrategySpec.from_dict({
        "name": "model", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "model", "ref": "invalid-rows-model-task-2"},
    })

    panel = build_signal_panel(spec, compile_strategy(spec, prices))

    assert pd.isna(panel.score.iloc[0, 0])
    assert pd.isna(panel.confidence.iloc[0, 0])
    assert pd.isna(panel.score.iloc[1, 0])
    assert pd.isna(panel.confidence.iloc[1, 0])
    assert any("as_of" in diagnostic for diagnostic in panel.diagnostics)
    assert any("confidence" in diagnostic for diagnostic in panel.diagnostics)


def test_model_provider_invalidates_nonfinite_prediction_with_out_of_range_confidence():
    class NonfiniteModel:
        def predict(self, features):
            return [float("nan")] * len(features)

    register_model(
        "nonfinite-confidence-model-task-2",
        NonfiniteModel(),
        {
            "model_id": "nonfinite-confidence-model-task-2",
            "feature_version": "features-2026-08-28",
            "model_version": "nonfinite-confidence-v1",
            "as_of": "2026-01-01T00:00:00+00:00",
            "confidence": 1.1,
        },
    )
    prices = pd.DataFrame({"AAPL": [100]}, index=pd.date_range("2026-01-01", periods=1))
    spec = StrategySpec.from_dict({
        "name": "model", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "model", "ref": "nonfinite-confidence-model-task-2"},
    })

    panel = build_signal_panel(spec, compile_strategy(spec, prices))

    assert pd.isna(panel.score.iloc[0, 0])
    assert pd.isna(panel.confidence.iloc[0, 0])
    assert any("confidence" in diagnostic for diagnostic in panel.diagnostics)
    assert any("prediction" in diagnostic for diagnostic in panel.diagnostics)


def test_model_provider_maps_multi_index_as_of_values_without_nan():
    class FixedModel:
        def predict(self, features):
            return [0.25] * len(features)

    index = pd.date_range("2026-01-01", periods=2)
    feature_index = pd.MultiIndex.from_product([index, ["AAPL"]], names=["timestamp", "symbol"])
    register_model(
        "multi-index-model-task-2",
        FixedModel(),
        {
            "model_id": "multi-index-model-task-2",
            "feature_version": "features-2026-08-28",
            "model_version": "multi-index-v1",
            "as_of": pd.Series(index, index=feature_index),
            "confidence": 0.7,
        },
    )
    prices = pd.DataFrame({"AAPL": [100, 101]}, index=index)
    spec = StrategySpec.from_dict({
        "name": "model", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "model", "ref": "multi-index-model-task-2"},
    })

    panel = build_signal_panel(spec, compile_strategy(spec, prices))

    assert panel.as_of["AAPL"].tolist() == list(index)
    assert not panel.as_of["AAPL"].isna().any()


def test_engine_fails_when_model_has_no_confidence_valid_rows():
    class FixedModel:
        def predict(self, features):
            return [0.25] * len(features)

    register_model(
        "no-confidence-model-task-2",
        FixedModel(),
        {
            "model_id": "no-confidence-model-task-2",
            "feature_version": "features-2026-08-28",
            "model_version": "no-confidence-v1",
            "as_of": "2026-01-01T00:00:00+00:00",
            "confidence": [float("nan"), float("nan")],
        },
    )
    prices = pd.DataFrame({"AAPL": [100, 101]}, index=pd.date_range("2026-01-01", periods=2))
    spec = {
        "name": "model", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "model", "ref": "no-confidence-model-task-2"},
    }

    run = run_strategy_backtest(spec, prices)

    assert run.ok is False
    assert any("confidence" in error for error in run.errors)


def test_provider_provenance_is_deterministic_and_ensemble_keeps_all_versions():
    scores = pd.DataFrame({"AAPL": [1.0]})
    rule = SignalPanel.from_score("rule", scores, confidence=0.8)
    momentum = SignalPanel.from_score("momentum", scores, confidence=0.8)
    model_one = SignalPanel(
        "model", scores, pd.DataFrame({"AAPL": [0.8]}),
        feature_version=pd.DataFrame({"AAPL": ["features-one"]}),
        model_version=pd.DataFrame({"AAPL": ["model-one"]}),
    )
    model_two = SignalPanel(
        "model", scores, pd.DataFrame({"AAPL": [0.8]}),
        feature_version=pd.DataFrame({"AAPL": ["features-two"]}),
        model_version=pd.DataFrame({"AAPL": ["model-two"]}),
    )

    assert rule.feature_version.iloc[0, 0] == "rule-v1"
    assert rule.model_version.iloc[0, 0] == ""
    combined = combine_signal_panels([rule, momentum, model_one, model_two], [1, 1, 1, 1])
    assert combined.feature_version.iloc[0, 0] == "rule-v1|momentum-v1|features-one|features-two"
    assert combined.model_version.iloc[0, 0] == "model-one|model-two"


def replace_signal_ref(spec, model_id):
    return StrategySpec.from_dict({**spec.to_dict(), "signal": {"type": "model", "ref": model_id}})


def test_signal_panel_validates_confidence_range_for_direct_construction():
    with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
        SignalPanel("test", pd.DataFrame({"AAPL": [1.0]}), pd.DataFrame({"AAPL": [1.1]}))


def test_ensemble_minimum_confidence_gate_invalidates_low_confidence_score():
    panel = combine_signal_panels(
        [SignalPanel.from_score("model", pd.DataFrame({"AAPL": [0.5]}), confidence=0.4)],
        weights=[1.0],
        min_confidence=0.55,
    )

    assert pd.isna(panel.score.iloc[0, 0])
    assert panel.confidence.iloc[0, 0] == 0.0
    assert panel.reason.iloc[0, 0] == "minimum confidence gate"


def test_ensemble_honors_an_explicit_zero_member_weight():
    panel = combine_signal_panels(
        [
            SignalPanel.from_score("first", pd.DataFrame({"AAPL": [1.0]}), confidence=1.0),
            SignalPanel.from_score("zero", pd.DataFrame({"AAPL": [-1.0]}), confidence=1.0),
        ],
        weights=[1.0, 0.0],
    )

    assert panel.score.iloc[0, 0] == pytest.approx(1.0)


def test_ensemble_normalizes_non_unit_weights():
    panel = combine_signal_panels(
        [
            SignalPanel.from_score("first", pd.DataFrame({"AAPL": [1.0]}), confidence=0.8),
            SignalPanel.from_score("second", pd.DataFrame({"AAPL": [-0.5]}), confidence=0.6),
        ],
        weights=[3.0, 1.0],
    )

    assert panel.score.iloc[0, 0] == pytest.approx(0.625)
    assert panel.confidence.iloc[0, 0] == pytest.approx(0.75)


def test_unknown_factor_provider_fails_backtest_with_diagnostic():
    prices = pd.DataFrame({"AAPL": [100, 101]}, index=pd.date_range("2026-01-01", periods=2))
    spec = {
        "name": "unknown provider", "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "factor", "plugin": "value"},
    }

    run = run_strategy_backtest(spec, prices)

    assert run.ok is False
    assert any("provider" in error and "not registered" in error for error in run.errors)
