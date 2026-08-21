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
# 단기진입 = intraday 표면(kr/us_intraday) — fwd_excess 는 R 단위
# 관측 = 랭킹 섀도 표면({kr,us}_mock_shadow) — 주문 없이 점수화된 전 후보(선택편향 없는 IC 측정)
_BUY = ("편입", "증액", "단기진입", "관측")

MIN_SAMPLES = int(os.getenv("EVOLVE_MIN_SAMPLES", "40"))   # 이 이상 성숙해야 판정 (validation 정직 규율)
IC_EDGE = float(os.getenv("EVOLVE_IC_EDGE", "0.05"))       # 이 이상 순비용 IC 지속 = 약한 엣지
# 새 결정이 이 일수 이상 안 나오면 "축적 중"이 아니라 "정체" — 기다림이 답이 아님을 명시.
# (2026-08 국내 사례: min_hold 60일+현금0+3종목 → 구조적으로 새 표본 불가인데 콜드스타트로만 표기)
STALL_DAYS = int(os.getenv("EVOLVE_STALL_DAYS", "10"))


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


def stall_days(decisions: list[dict], today: str | None = None) -> int | None:
    """마지막 결정 이후 경과일 — 표본이 실제로 늘고 있는지 판별. 결정 없으면 None. 순수.

    콜드스타트(축적 중)와 정체(구조적으로 멈춤)를 구분하는 유일한 계기.
    """
    import datetime as _dt

    dates = []
    for d in decisions or []:
        raw = str((d or {}).get("date") or "")[:10]
        try:
            dates.append(_dt.date.fromisoformat(raw))
        except (ValueError, TypeError):
            continue
    if not dates:
        return None
    try:
        t0 = _dt.date.fromisoformat(str(today)[:10]) if today else _dt.date.today()
    except (ValueError, TypeError):
        t0 = _dt.date.today()
    return (t0 - max(dates)).days


def ic_ci(ic: float | None, n: int, z: float = 1.96) -> list[float] | None:
    """IC 95% 신뢰구간 (Fisher z 변환). n<4 또는 ic None 이면 None. 순수.

    소표본 IC 는 신뢰구간이 극단적으로 넓다(n=14 면 ±0.5 대) — 점추정만 보면 "IC≈0 이니
    엣지 없음"으로 오독하기 쉬워, 구간을 함께 노출해 과소·과대 주장 양쪽을 막는다.
    """
    if ic is None or n is None or n < 4:
        return None
    r = max(-0.999999, min(0.999999, float(ic)))
    zr = 0.5 * math.log((1 + r) / (1 - r))
    se = 1.0 / math.sqrt(n - 3)
    lo, hi = zr - z * se, zr + z * se
    return [round(math.tanh(lo), 3), round(math.tanh(hi), 3)]


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
    return {"n": n, "realized_ic": ic, "buy_hit": hit, "cum_net_excess": cum,
            "ic_ci": ic_ci(ic, len(pairs))}


def axis_ic(training_rows: list[dict], min_pairs: int = 5) -> dict:
    """피처 축별 IC — 어느 축이 실제로 편입/편출 결과를 예측하나. 순수.

    반환 {axis: {ic, ci, n}} (표본 min_pairs 미만 축은 제외).

    배경: 국내 정책 가중치의 0.65(ranker .30 + fund .15 + signal .15 + conf .05)는
    25년 정밀 백테스트(backtest/kr_policy_backtest.py) 검증 대상에 아예 없었다 —
    그 백테스트는 가격 축(mom12·hi52·vol_inv 등)만 다루기 때문. 손으로 정한 가중치가
    실제로 예측력이 있는지는 미검증 상태였고, 랭킹 섀도가 쌓이면 여기서 직접 측정된다.
    """
    buys = [r for r in (training_rows or [])
            if r.get("side") in _BUY and r.get("fwd_excess") is not None
            and isinstance(r.get("features"), dict)]
    axes: dict[str, list] = {}
    for r in buys:
        for k, v in (r.get("features") or {}).items():
            if v is None:
                continue
            try:
                axes.setdefault(k, []).append((float(v), float(r["fwd_excess"])))
            except (TypeError, ValueError):
                continue
    out: dict[str, dict] = {}
    for k, pairs in axes.items():
        if len(pairs) < max(3, min_pairs):
            continue
        ic = _pearson([a for a, _ in pairs], [b for _, b in pairs])
        if ic is None:
            continue
        out[k] = {"ic": ic, "ci": ic_ci(ic, len(pairs)), "n": len(pairs)}
    return out


def verdict(snap: dict, history: list[dict] | None = None,
            stall_days: int | None = None) -> dict:
    """정직 분류 — 콜드스타트/정체/관찰중/약한엣지/무엣지. 과대 엣지 주장 방지.

    stall_days: 마지막 결정 이후 경과일(stall_days()). 표본 미달인데 이 값이 STALL_DAYS
    이상이면 "축적 중"이 아니라 **정체**로 표기 — 기다려도 표본이 안 늘어난다는 사실을 숨기지 않음.
    """
    n = snap.get("n") or 0
    ic = snap.get("realized_ic")
    cum = snap.get("cum_net_excess") or 0.0
    ci = snap.get("ic_ci")
    if n < MIN_SAMPLES:
        if stall_days is not None and stall_days >= STALL_DAYS:
            return {"code": "stalled", "emoji": "🧊", "label": f"정체 (성숙 {n}/{MIN_SAMPLES})",
                    "note": f"{stall_days}일째 새 결정 0건 — 축적이 멈춤 (대기로는 해결 안 됨)"}
        return {"code": "cold", "emoji": "🌱", "label": f"콜드스타트 (성숙 {n}/{MIN_SAMPLES})",
                "note": "데이터 축적 중 — 학습 전 (정상)"}
    if ic is None:
        return {"code": "observe", "emoji": "👀", "label": "관찰 중", "note": "IC 산출 표본 부족"}
    insignificant = bool(ci and ci[0] <= 0 <= ci[1])
    ci_note = f" · 95%CI [{ci[0]:+.2f},{ci[1]:+.2f}] 0 포함=유의하지 않음" if insignificant else ""
    if ic >= IC_EDGE and cum > 0:
        return {"code": "edge", "emoji": "🧬", "label": "약한 엣지 형성",
                "note": f"순비용 IC {ic:+.3f}·누적 {cum:+.2%} (신뢰 낮음·표본 주의){ci_note}"}
    if abs(ic) < IC_EDGE and cum <= 0:
        return {"code": "noedge", "emoji": "➖", "label": "무엣지 (정직)",
                "note": f"순비용 IC {ic:+.3f}≈0 — 선택 스킬 미확인{ci_note}"}
    return {"code": "observe", "emoji": "👀", "label": "관찰 중",
            "note": f"순비용 IC {ic:+.3f}·누적 {cum:+.2%}{ci_note}"}


def evolution_summary(surface: str, training_rows: list[dict], base_dir: str | None = None,
                      decisions: list[dict] | None = None, today: str | None = None) -> dict:
    """스냅샷 + 이력 → 렌더용 통합. /evolve·대시보드 공용.

    decisions: 원장의 **원시 결정**(미성숙 포함) — 주면 정체(stall) 판정까지 수행한다.
    training_rows(성숙분)만으론 마지막 '성숙일'이 찍혀 실제 정체를 과소평가하므로 분리.
    """
    hist = read_learning(surface, base_dir)
    snap = snapshot(training_rows)
    sd = stall_days(decisions, today) if decisions is not None else None
    series = [{"date": h.get("date"), "excess": h.get("excess_challenger"),
               "ic": h.get("realized_ic"), "adopted": bool(h.get("adopted"))} for h in hist]
    adoptions = [h for h in hist if h.get("adopted")]
    return {"surface": surface, "snapshot": snap, "verdict": verdict(snap, hist, stall_days=sd),
            "series": series, "adoptions": adoptions, "n_runs": len(hist), "stall_days": sd}
