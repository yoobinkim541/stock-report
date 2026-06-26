#!/usr/bin/env python3
"""test_adaptive_hardening.py — Phase 0 §F 하드닝 (무네트워크).

핵심 = **F1 죽은 shadow 배선** 검증: 그 전엔 longterm_policy_shadow / advice_blend_shadow 가
write-only 죽은 파일이라 ADAPTIVE_*_ENABLED 를 켜도 라이브에 아무 효과가 없었음. 이제:
  - 기본 OFF → 라이브 불변(게이트),
  - ON → clamp 범위 내에서만(위험 축소/기존 envelope 내) 반영.
추가로 F2 entry 적응: 시계열(triggered_at) 분할 + 동점 시 보수적 임계 타이브레이크.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _write_shadow(tmp_path, name, obj):
    d = tmp_path / "reports" / "ml-cache"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(obj), encoding="utf-8")


# ── F1: 장기 shadow → leverage_signal.lev_scale (Phase 3 RL 배선) ─────────────
def test_longterm_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ADAPTIVE_LONGTERM_ENABLED", raising=False)
    _write_shadow(tmp_path, "longterm_policy_shadow.json", {"lev_scale": 0.6})
    from ml import leverage_signal
    assert leverage_signal._adaptive_longterm_scale() == 1.0   # off → shadow 무시(라이브 불변)


def test_longterm_applied_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ADAPTIVE_LONGTERM_ENABLED", "true")
    _write_shadow(tmp_path, "longterm_policy_shadow.json", {"lev_scale": 0.7})
    from ml import leverage_signal
    assert leverage_signal._adaptive_longterm_scale() == 0.7   # 옵트인 → 적용


def test_longterm_clamped_reduction_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ADAPTIVE_LONGTERM_ENABLED", "true")
    from ml import leverage_signal
    _write_shadow(tmp_path, "longterm_policy_shadow.json", {"lev_scale": 0.1})
    assert leverage_signal._adaptive_longterm_scale() == 0.5    # 0.5 하한
    _write_shadow(tmp_path, "longterm_policy_shadow.json", {"lev_scale": 1.8})
    assert leverage_signal._adaptive_longterm_scale() == 1.0    # 증액 불가(≤1.0)


def test_longterm_missing_or_corrupt_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ADAPTIVE_LONGTERM_ENABLED", "true")
    from ml import leverage_signal
    assert leverage_signal._adaptive_longterm_scale() == 1.0    # 파일 없음 → 안전
    d = tmp_path / "reports" / "ml-cache"
    d.mkdir(parents=True, exist_ok=True)
    (d / "longterm_policy_shadow.json").write_text("{corrupt", encoding="utf-8")
    assert leverage_signal._adaptive_longterm_scale() == 1.0    # 손상 → 안전


# ── F1: advice shadow → barbell._phase_blend_factor (Phase 4 RL 배선) ─────────
def test_advice_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ADAPTIVE_ADVICE_ENABLED", raising=False)
    _write_shadow(tmp_path, "advice_blend_shadow.json", {"blend": 0.6})
    import barbell_strategy
    assert barbell_strategy._adaptive_advice_blend(0.2) == 0.2   # off → base(라이브 불변)


def test_advice_raises_within_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ADAPTIVE_ADVICE_ENABLED", "true")
    import barbell_strategy
    _write_shadow(tmp_path, "advice_blend_shadow.json", {"blend": 0.5})
    assert barbell_strategy._adaptive_advice_blend(0.2) == 0.5    # 입증된 meta 로 상향
    _write_shadow(tmp_path, "advice_blend_shadow.json", {"blend": 0.9})
    assert barbell_strategy._adaptive_advice_blend(0.2) == 0.6    # 기존 0.6 상한 내로 클램프
    _write_shadow(tmp_path, "advice_blend_shadow.json", {"blend": 0.1})
    assert barbell_strategy._adaptive_advice_blend(0.3) == 0.3    # base 미만으로는 안 내림


def test_advice_missing_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ADAPTIVE_ADVICE_ENABLED", "true")
    import barbell_strategy
    assert barbell_strategy._adaptive_advice_blend(0.25) == 0.25  # 파일 없음 → base


# ── F2: entry_adaptive 시계열 분할 + 보수적 타이브레이크 ──────────────────────
def test_entry_samples_sorted_by_triggered_at(monkeypatch):
    from crons import entry_adaptive_learn as e
    rows = [   # 일부러 역순 + 무타임스탬프 1건
        {"score": 0.7, "r_multiple": 2.0, "triggered_at": "2026-03-01T00:00:00"},
        {"score": 0.5, "r_multiple": -1.0, "triggered_at": "2026-01-01T00:00:00"},
        {"score": 0.6, "r_multiple": 1.0, "registered_at": "2026-02-01T00:00:00"},
        {"score": 0.8, "r_multiple": 3.0},   # 타임스탬프 없음 → 맨 뒤
    ]
    monkeypatch.setattr("store.all", lambda coll: list(rows))
    out = e._samples()
    assert [rm for _, rm in out] == [-1.0, 1.0, 2.0, 3.0]   # 1월→2월→3월→무타임스탬프


def test_entry_best_threshold_prefers_stricter_on_tie():
    from crons import entry_adaptive_learn as e
    samples = [(0.9, 1.0)] * 10   # 모든 임계에서 동일 진입군·동일 평균R → 동점
    assert e._best_threshold(samples) == e.THRESH_GRID[-1]   # 동점 → 최고(가장 보수적) 임계


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
