import pandas as pd

from ml import technical_rating as tr


def test_reference_brief_formats_krw_pivots_and_interpretation(monkeypatch):
    df = pd.DataFrame(
        {"Close": [2_103_000.0]},
        index=pd.date_range("2026-07-08", periods=1),
    )
    monkeypatch.setattr(
        tr,
        "compute_technical_rating",
        lambda _df: {
            "summary": {"rating": "🔴 매도"},
            "ma": {"buy": 4, "sell": 9},
            "osc": {"buy": 1, "sell": 3},
        },
    )
    monkeypatch.setattr(
        tr,
        "pivot_points",
        lambda _df: {"P": 2_497_333.33, "S1": 2_007_666.67, "R1": 3_139_666.67},
    )

    brief = tr.build_reference_brief("000660.KS", df, include_options=False)

    assert "기술등급: 🔴 매도 (이평 4↑/9↓ · 오실 1↑/3↓)" in brief
    assert "월 피벗:  P ₩2,497,333 | S1 ₩2,007,667 / R1 ₩3,139,667" in brief
    assert "기술 추세는 아직 약세라 통계 신호와 충돌" in brief
    assert "월 피벗(₩2,497,333) 회복 전까지 반등 신뢰도 낮음" in brief
