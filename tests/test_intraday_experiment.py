from ml.intraday_experiment import (
    DecisionSnapshot,
    RiskGovernor,
    label_lifecycle_decision,
    label_shadow_decision,
)
import pytest


def test_label_shadow_decision_creates_multiple_horizons():
    decision = DecisionSnapshot(
        id="d1",
        timestamp="2026-07-28T09:00:00+09:00",
        symbol="005930",
        session_phase="open",
        features={"price": 70000},
        signals={"momentum": 0.7},
        decision="long",
        position_context={"current_qty": 0},
        expected_edge=0.004,
        risk_budget=0.01,
        cost_estimate=0.001,
        reason_codes=["open_momentum"],
    )
    prices = [
        {"minute": 0, "price": 70000},
        {"minute": 5, "price": 70400},
        {"minute": 15, "price": 69800},
        {"minute": 30, "price": 70700},
    ]

    labels = label_shadow_decision(decision, prices, horizons=(5, 15, 30))

    assert [label.horizon for label in labels] == ["5m", "15m", "30m"]
    assert round(labels[0].realized_return, 6) == round((70400 / 70000) - 1 - 0.001, 6)
    assert labels[1].quality_label == "bad"
    assert labels[2].max_favorable_excursion > 0


def test_risk_governor_does_not_count_multiple_horizons_as_multiple_losses():
    decision = DecisionSnapshot(
        id="d1",
        timestamp="2026-07-28T09:00:00+09:00",
        symbol="005930",
        session_phase="open",
        features={"price": 70000},
        signals={"momentum": 0.7},
        decision="long",
        position_context={},
        expected_edge=0.004,
        risk_budget=0.01,
        cost_estimate=0.001,
        reason_codes=["open_momentum"],
    )
    labels = label_shadow_decision(decision, [
        {"minute": 0, "price": 70000},
        {"minute": 5, "price": 69000},
        {"minute": 15, "price": 68800},
        {"minute": 30, "price": 68700},
    ])

    result = RiskGovernor(max_bad_labels=2).assess([decision], labels, data_fresh=True)

    assert result == {"action": "allow", "reasons": []}


def test_risk_governor_blocks_stale_data_or_consecutive_losing_decisions():
    decisions = [
        DecisionSnapshot(
            id=f"d{index}",
            timestamp=f"2026-07-28T09:{index:02d}:00+09:00",
            symbol="005930",
            session_phase="open",
            features={"price": 70000},
            signals={"momentum": 0.7},
            decision="long",
            position_context={},
            expected_edge=0.004,
            risk_budget=0.01,
            cost_estimate=0.001,
            reason_codes=["open_momentum"],
        )
        for index in (1, 2)
    ]
    labels = [
        label
        for decision in decisions
        for label in label_shadow_decision(decision, [
            {"minute": 0, "price": 70000},
            {"minute": 5, "price": 69000},
            {"minute": 15, "price": 68800},
            {"minute": 30, "price": 68700},
        ])
    ]

    stale = RiskGovernor(max_bad_labels=2).assess(decisions, labels, data_fresh=False)
    loss_cluster = RiskGovernor(max_bad_labels=2).assess(decisions, labels, data_fresh=True)

    assert stale["action"] == "shadow_only"
    assert "stale_data" in stale["reasons"]
    assert loss_cluster["action"] == "size_down"
    assert "loss_cluster" in loss_cluster["reasons"]


def test_risk_governor_requires_losses_to_be_consecutive():
    decisions = [
        DecisionSnapshot(
            id=f"d{index}",
            timestamp=f"2026-07-28T09:{index:02d}:00+09:00",
            symbol="005930",
            session_phase="open",
            features={"price": 70000},
            signals={},
            decision="long",
            position_context={},
            expected_edge=0.004,
            risk_budget=0.01,
            cost_estimate=0.001,
        )
        for index in (1, 2, 3)
    ]
    exit_prices = (69000, 71000, 69000)
    labels = [
        label
        for decision, exit_price in zip(decisions, exit_prices)
        for label in label_shadow_decision(
            decision,
            [{"minute": 0, "price": 70000}, {"minute": 30, "price": exit_price}],
            horizons=(30,),
        )
    ]

    result = RiskGovernor(max_bad_labels=2).assess(decisions, labels, data_fresh=True)

    assert result == {"action": "allow", "reasons": []}


def test_label_shadow_decision_marks_missing_horizon_pending():
    decision = DecisionSnapshot(
        id="d2",
        timestamp="2026-07-28T09:00:00+09:00",
        symbol="005930",
        session_phase="open",
        features={"price": 70000},
        signals={},
        decision="long",
        position_context={},
        expected_edge=0.004,
        risk_budget=0.01,
        cost_estimate=0.001,
    )

    labels = label_shadow_decision(
        decision,
        [{"minute": 0, "price": 70000}, {"minute": 5, "price": 70400}],
        horizons=(5, 15),
    )

    assert [label.horizon for label in labels] == ["5m", "15m"]
    assert labels[1].quality_label == "pending"
    assert labels[1].pending_reason == "missing_price"
    assert labels[1].realized_return == 0.0
    assert labels[1].max_adverse_excursion == 0.0
    assert labels[1].max_favorable_excursion == 0.0


def test_label_shadow_decision_rejects_malformed_decision():
    decision = DecisionSnapshot(
        id="d3",
        timestamp="2026-07-28T09:00:00+09:00",
        symbol="005930",
        session_phase="open",
        features={"price": 70000},
        signals={},
        decision="hold",
        position_context={},
        expected_edge=0.004,
        risk_budget=0.01,
        cost_estimate=0.001,
    )

    with pytest.raises(ValueError, match="long.*short"):
        label_shadow_decision(decision, [{"minute": 0, "price": 70000}])


def test_label_shadow_decision_marks_null_horizon_price_pending():
    decision = DecisionSnapshot(
        id="d4",
        timestamp="2026-07-28T09:00:00+09:00",
        symbol="005930",
        session_phase="open",
        features={"price": 70000},
        signals={},
        decision="long",
        position_context={},
        expected_edge=0.004,
        risk_budget=0.01,
        cost_estimate=0.001,
    )

    labels = label_shadow_decision(
        decision,
        [{"minute": 0, "price": 70000}, {"minute": 5, "price": None}],
        horizons=(5,),
    )

    assert len(labels) == 1
    assert labels[0].quality_label == "pending"
    assert labels[0].pending_reason == "missing_price"


def test_label_shadow_decision_marks_missing_entry_price_pending():
    decision = DecisionSnapshot(
        id="d5",
        timestamp="2026-07-28T09:00:00+09:00",
        symbol="005930",
        session_phase="open",
        features={},
        signals={},
        decision="long",
        position_context={},
        expected_edge=0.004,
        risk_budget=0.01,
        cost_estimate=0.001,
    )

    labels = label_shadow_decision(
        decision,
        [{"minute": 0, "price": None}, {"minute": 5, "price": 70400}],
        horizons=(5, 15),
    )

    assert [label.horizon for label in labels] == ["5m", "15m"]
    assert all(label.quality_label == "pending" for label in labels)
    assert all(label.pending_reason == "missing_entry_price" for label in labels)


def test_label_lifecycle_decision_records_partial_target_then_full_target():
    decision = DecisionSnapshot(
        id="life1",
        timestamp="2026-07-28T09:00:00+09:00",
        symbol="005930",
        session_phase="open",
        features={"price": 100.0},
        signals={},
        decision="long",
        position_context={"qty": 10},
        expected_edge=2.0,
        risk_budget=2.0,
        cost_estimate=0.0,
    )

    label = label_lifecycle_decision(
        decision,
        [
            {"minute": 0, "price": 100.0},
            {"minute": 1, "high": 102.2, "low": 100.5, "price": 101.8},
            {"minute": 2, "high": 104.5, "low": 101.5, "price": 104.0},
        ],
        cfg={"partial_exit_min_hold_min": 0},
    )

    assert label.partial_target_hit is True
    assert label.full_target_hit is True
    assert label.partial_stop_hit is False
    assert label.full_stop_hit is False
    assert label.quality_label == "good"
    assert label.realized_r > 1.0


def test_label_lifecycle_decision_prefers_full_stop_when_same_bar_hits_both():
    decision = DecisionSnapshot(
        id="life2",
        timestamp="2026-07-28T09:00:00+09:00",
        symbol="005930",
        session_phase="open",
        features={"price": 100.0},
        signals={},
        decision="long",
        position_context={"qty": 10},
        expected_edge=2.0,
        risk_budget=2.0,
        cost_estimate=0.0,
    )

    label = label_lifecycle_decision(decision, [
        {"minute": 0, "price": 100.0},
        {"minute": 1, "high": 104.5, "low": 97.8, "price": 100.0},
    ])

    assert label.full_stop_hit is True
    assert label.full_target_hit is False
    assert label.quality_label == "bad"
