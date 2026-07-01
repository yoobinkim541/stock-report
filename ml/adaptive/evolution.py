"""ml/adaptive/evolution.py — 모의 자기개선 "진화" 텔레메트리.

주간 재학습이 만드는 데이터(채택여부·챔피언/챌린저 OOS·정책가중치)를 **append-only 이력**으로
남기고(`{surface}_learning.jsonl`), 현재 원장의 라이브 스냅샷과 합쳐 **정직한 진화 verdict**를 낸다.
정직 규율: 표본 부족이면 "콜드스타트", 충분해도 순비용 IC≈0 이면 "무엣지"로 공개(과대주장 0).
순수 함수(snapshot·verdict) + append/read I/O. fwd_excess 는 P3 이후 **순비용**(수수료·세금 차감).
"""
from __future__ import annotations

import json
import math
import os

_DIR = os.path.expanduser("~/reports/ml-data")
_BUY = ("편입", "증액")

MIN_SAMPLES = int(os.getenv("EVOLVE_MIN_SAMPLES", "40"))   # 이 이상 성숙해야 판정 (validation 정직 규율)
IC_EDGE = float(os.getenv("EVOLVE_IC_EDGE", "0.05"))       # 이 이상 순비용 IC 지속 = 약한 엣지


def _path(surface: str, base_dir: str | None = None) -> str:
    return os.path.join(base_dir or _DIR, f"{surface}_learning.jsonl")


def record_learning(surface: str, rec: dict, base_dir: str | None = None) -> None:
    """주간 학습 결과 1건 append (삭제 금지·감사). ledger JSONL 패턴."""
    d = base_dir or _DIR
    try:
        os.makedirs(d, exist_ok=True)
        with open(_path(surface, d), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_learning(surface: str, base_dir: str | None = None) -> list[dict]:
    """학습 이력 전체(오래된→최근). 없으면 []."""
    p = _path(surface, base_dir)
    if not os.path.exists(p):
        return []
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
    except Exception:
        return []
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return round(num / (dx * dy), 3) if dx > 0 and dy > 0 else None


def snapshot(training_rows: list[dict]) -> dict:
    """원장(결정⋈결과) → 라이브 스냅샷. 순비용 fwd_excess 기준. 순수.

    {n(성숙 매수), realized_ic(policy_score↔순초과), buy_hit%, cum_net_excess(평균 순초과)}.
    """
    buys = [r for r in (training_rows or [])
            if r.get("side") in _BUY and r.get("fwd_excess") is not None]
    n = len(buys)
    pairs = [(r.get("policy_score"), r.get("fwd_excess")) for r in buys
             if r.get("policy_score") is not None]
    ic = _pearson([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) >= 3 else None

    def _ok(r):
        v = r.get("correct")
        return r.get("success") if v is None else v
    judged = [r for r in buys if _ok(r) is not None]
    hit = round(sum(1 for r in judged if _ok(r)) / len(judged) * 100.0, 1) if judged else None
    cum = round(sum(r["fwd_excess"] for r in buys) / n, 4) if n else None
    return {"n": n, "realized_ic": ic, "buy_hit": hit, "cum_net_excess": cum}


def verdict(snap: dict, history: list[dict] | None = None) -> dict:
    """정직 분류 — 콜드스타트/관찰중/약한엣지/무엣지. 과대 엣지 주장 방지."""
    n = snap.get("n") or 0
    ic = snap.get("realized_ic")
    cum = snap.get("cum_net_excess") or 0.0
    if n < MIN_SAMPLES:
        return {"code": "cold", "emoji": "🌱", "label": f"콜드스타트 (성숙 {n}/{MIN_SAMPLES})",
                "note": "데이터 축적 중 — 학습 전 (정상)"}
    if ic is None:
        return {"code": "observe", "emoji": "👀", "label": "관찰 중", "note": "IC 산출 표본 부족"}
    if ic >= IC_EDGE and cum > 0:
        return {"code": "edge", "emoji": "🧬", "label": "약한 엣지 형성",
                "note": f"순비용 IC {ic:+.3f}·누적 {cum:+.2%} (신뢰 낮음·표본 주의)"}
    if abs(ic) < IC_EDGE and cum <= 0:
        return {"code": "noedge", "emoji": "➖", "label": "무엣지 (정직)",
                "note": f"순비용 IC {ic:+.3f}≈0 — 선택 스킬 미확인"}
    return {"code": "observe", "emoji": "👀", "label": "관찰 중",
            "note": f"순비용 IC {ic:+.3f}·누적 {cum:+.2%}"}


def evolution_summary(surface: str, training_rows: list[dict], base_dir: str | None = None) -> dict:
    """스냅샷 + 이력 → 렌더용 통합. /evolve·대시보드 공용."""
    hist = read_learning(surface, base_dir)
    snap = snapshot(training_rows)
    series = [{"date": h.get("date"), "excess": h.get("excess_challenger"),
               "ic": h.get("realized_ic"), "adopted": bool(h.get("adopted"))} for h in hist]
    adoptions = [h for h in hist if h.get("adopted")]
    return {"surface": surface, "snapshot": snap, "verdict": verdict(snap, hist),
            "series": series, "adoptions": adoptions, "n_runs": len(hist)}
