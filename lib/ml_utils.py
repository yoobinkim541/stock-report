"""lib/ml_utils.py — 공유 ML 피처 유틸 (ml 모델 중복 제거, 행위 보존).

deletion_risk·earnings_predictor·earnings_move_predictor 가 동일하게 반복하던 rows→행렬 변환 통합.
(train/OOS 로직은 모델별로 본질적으로 달라(분류 vs 회귀+분류·지표·게이트 상이) 공유하지 않음 —
 과추상 방지. 진짜 동일한 _matrix 만 추출.)
"""
from __future__ import annotations


def rows_to_matrix(rows: list[dict], cols: list[str]) -> list[list]:
    """[{features:{col: val}}] → X 2차원 리스트. 결측은 nan(LightGBM 처리 가능)."""
    return [[r["features"].get(c) if r["features"].get(c) is not None else float("nan")
             for c in cols] for r in rows]
