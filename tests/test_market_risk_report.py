import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reports"))

from market_risk_report import build_market_risk_report, classify_regime, build_mobile_summary


def _sample_inputs():
    return {
        "as_of": "2026-04-02 20:55 KST",
        "assets": {
            "SPY": {"last": 655.24, "d1": 0.75, "d5": -0.24, "d20": -4.36, "d60": -4.72, "as_of": "2026-04-01"},
            "QQQ": {"last": 584.31, "d1": 1.24, "d5": -0.60, "d20": -4.33, "d60": -5.45, "as_of": "2026-04-01"},
            "IWM": {"last": 249.56, "d1": 0.63, "d5": -0.90, "d20": -4.66, "d60": -1.25, "as_of": "2026-04-01"},
            "RSP": {"last": 192.54, "d1": 0.32, "d5": -0.16, "d20": -5.10, "d60": -1.08, "as_of": "2026-04-01"},
            "^VIX": {"last": 27.62, "d1": 12.55, "d5": 0.66, "d20": 16.29, "d60": 87.25, "as_of": "2026-04-02"},
            "^MOVE": {"last": 90.19, "d1": -6.10, "d5": -7.58, "d20": 28.79, "d60": 39.38, "as_of": "2026-04-01"},
            "TLT": {"last": 86.26, "d1": -0.50, "d5": -0.67, "d20": -3.24, "d60": -1.37, "as_of": "2026-04-01"},
            "HYG": {"last": 79.37, "d1": -0.24, "d5": -0.06, "d20": -1.28, "d60": -1.87, "as_of": "2026-04-01"},
            "LQD": {"last": 108.66, "d1": -0.30, "d5": -0.06, "d20": -2.08, "d60": -1.64, "as_of": "2026-04-01"},
            "CL=F": {"last": 109.35, "d1": 9.22, "d5": 15.74, "d20": 34.98, "d60": 91.41, "as_of": "2026-04-02"},
            "DX-Y.NYB": {"last": 100.22, "d1": 0.57, "d5": 0.32, "d20": 0.91, "d60": 1.66, "as_of": "2026-04-02"},
            "BTC-USD": {"last": 66338, "d1": -2.56, "d5": 0.03, "d20": -6.52, "d60": -13.82, "as_of": "2026-04-02"},
            "ES=F": {"last": 6514.0, "d1": -1.54, "d5": -1.0, "d20": -4.0, "d60": -4.0, "as_of": "2026-04-02"},
            "NQ=F": {"last": 23712.25, "d1": -1.72, "d5": -1.2, "d20": -4.0, "d60": -5.0, "as_of": "2026-04-02"},
            "RTY=F": {"last": 2475.4, "d1": -2.27, "d5": -1.8, "d20": -4.5, "d60": -2.0, "as_of": "2026-04-02"},
        },
        "world_memory": [
            {"title": "중동 리스크와 에너지 가격", "meaning": "공급·물류 차질", "implication": "유가와 마진 압박"},
            {"title": "선진국 금리 인하 지연", "meaning": "물가 상방", "implication": "장기채와 멀티플 부담"},
        ],
        "news_digest": ["WTI 109달러 돌파", "호르무즈 재개방 논의", "관세 집행 신호"],
    }


def test_classify_regime_detects_energy_shock_riskoff():
    regime = classify_regime(_sample_inputs())

    assert "에너지" in regime["label"]
    assert regime["severity"] in {"중간", "높음"}
    assert "WTI" in " ".join(regime["drivers"])


def test_build_market_risk_report_uses_flash_and_deep_dive_layers():
    report = build_market_risk_report(_sample_inputs())

    assert "시장 위험 보고서" in report
    assert "Flash Layer" in report
    assert "Deep-Dive Layer" in report
    assert "데이터 컷오프" in report
    assert "버킷별 핵심 신호" in report
    assert "시나리오와 트리거" in report
    assert "이번 주 체크포인트" in report
    assert "WTI" in report and "VIX" in report and "HYG/LQD" in report


def test_build_mobile_summary_is_short_and_actionable():
    summary = build_mobile_summary(_sample_inputs())

    assert summary.startswith("⚠️ 시장 위험 보고서")
    assert "레짐:" in summary
    assert "핵심 체크" in summary
    assert len(summary) < 1800
