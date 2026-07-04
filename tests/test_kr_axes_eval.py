"""tests/test_kr_axes_eval.py — KR 가격축 재검증 크론 메시지·shadow (무네트워크)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from crons import kr_axes_eval as E


_RES = {
    "verdict": {"label": "👀 OBSERVE — OOS 순초과>0 이나 통계/MDD 관문 미달 (엣지 주장 불가)",
                "code": "OBSERVE", "dsr": 0.095, "pbo": 0.175,
                "oos": {"cagr": 0.157, "mdd": 0.483}, "bench": {"cagr": 0.102, "mdd": 0.541}},
    "recommendation": {"chosen": "hi52", "train_obj": 1.2,
                       "policy_weights": {"w_hi52": 0.35, "w_lowvol": 0.0, "w_mom12": 0.0, "w_mom": 0.0},
                       "window": ["2021-07-01", "2026-07-01"]},
}


def test_build_message_contains_verdict_and_recommendation():
    msg = E.build_message(_RES, enabled=False, shadow_written=False)
    assert "OBSERVE" in msg and "hi52" in msg
    assert "ADAPTIVE_KR_AXES_ENABLED=false" in msg          # off 명시
    assert "실계좌 자동집행 0" in msg                        # 안전 라벨


def test_build_message_enabled_shadow_written():
    msg = E.build_message(_RES, enabled=True, shadow_written=True)
    assert "✅ 기록" in msg and "모의 선택 전용" in msg


def test_build_message_no_recommendation():
    res = {**_RES, "recommendation": None}
    msg = E.build_message(res, enabled=True, shadow_written=False)
    assert "권고 없음" in msg


def test_save_shadow_roundtrip(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(E, "SHADOW_PATH", tmp_path / "shadow.json")
    E._save_shadow(_RES["recommendation"], "OBSERVE")
    d = json.loads((tmp_path / "shadow.json").read_text(encoding="utf-8"))
    assert d["chosen"] == "hi52" and d["verdict_code"] == "OBSERVE"
    assert d["policy_weights"]["w_hi52"] == 0.35 and d["asof"]
