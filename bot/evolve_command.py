"""bot/evolve_command.py — /evolve: 모의 자기개선 "진화" 통합 리포트 (KR+US 한 커맨드).

주간 학습 이력(evolution.read_learning) + 라이브 원장 스냅샷(Ledger.training_set → 순비용 IC·
적중률·누적엣지)을 합쳐 **정직한 진화 verdict**를 낸다. owner 전용·표시형·read-only.
정직: 표본 부족이면 "콜드스타트", 충분해도 순비용 IC≈0 이면 "무엣지" 공개(과대주장 0).
"""
from __future__ import annotations

import os

# 오프라인 정밀 백테스트(20~25년·DSR/PBO) 결과 위치 — backtest/{kr,us}_policy_backtest.py 산출.
_BACKTEST_DIR = os.path.expanduser("~/reports/ml-cache")


def _offline_verdict_line(surface: str) -> str | None:
    """오프라인 25년 백테스트 판정 한 줄. 없으면 None.

    라이브 콜드스타트를 단독으로 보면 "아직 모른다" 로 읽히지만, 같은 선택 로직의
    가격축은 이미 20~25년 OOS 로 검증돼 있다(KR OBSERVE·US NO-GO) — 병기해서
    '기다리면 좋아질 것'이라는 낙관 편향을 막는다.
    """
    market = "kr" if surface.startswith("kr") else "us"
    path = os.path.join(_BACKTEST_DIR, f"{market}_policy_backtest.json")
    try:
        import json
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    v = (d or {}).get("verdict") or {}
    code = v.get("code")
    if not code:
        return None
    bits = [f"🧪 오프라인({d.get('period', '?')}) {code}"]
    if v.get("net_excess_cagr") is not None:
        bits.append(f"순초과 {v['net_excess_cagr'] * 100:+.2f}%/yr")
    if v.get("dsr") is not None:
        bits.append(f"DSR {v['dsr']:.3f}")
    if v.get("pbo") is not None:
        bits.append(f"PBO {v['pbo'] * 100:.1f}%")
    if v.get("mdd_ok") is False:
        bits.append("MDD 위반")
    return "  " + " · ".join(bits)


def _surface_block(surface: str, flag: str, name: str, html: bool) -> list[str]:
    import fmt
    from ml.adaptive import Ledger, evolution
    _B = fmt.b if html else (lambda x: x)
    try:
        _ledger = Ledger(surface)
        rows = _ledger.training_set()
        decisions = _ledger.read_decisions()      # 원시 결정 — 정체(stall) 판정용
    except Exception:
        rows, decisions = [], None
    ev = evolution.evolution_summary(surface, rows, decisions=decisions)
    snap, v = ev["snapshot"], ev["verdict"]

    lines = [f"{flag} {_B(name)} — {v['emoji']} {_B(v['label'])}", f"  {v['note']}"]
    if surface.endswith("_intraday"):
        lines[-1] += "  (단기 — 수치는 R 단위·트레이드당)"
        gate = next((h.get("gate") for h in reversed(evolution.read_learning(surface))
                     if h.get("gate")), None)
        if gate:
            lines.append(f"  🚦게이트 {gate.get('verdict')} — n {gate.get('n')}"
                         f"·순R {gate.get('mean_net_r')}·PSR {gate.get('psr')}·PBO {gate.get('pbo')}")

    bits = [f"성숙 {snap.get('n', 0)}건"]
    if snap.get("realized_ic") is not None:
        bits.append(f"순비용 IC {snap['realized_ic']:+.3f}")
    if snap.get("buy_hit") is not None:
        bits.append(f"적중 {snap['buy_hit']:.0f}%")
    if snap.get("cum_net_excess") is not None:
        bits.append(f"누적 {snap['cum_net_excess'] * 100:+.2f}%")
    lines.append("  " + " · ".join(bits))

    # 랭킹 섀도 — 주문된 상위 N 만 보면 점수 분산이 잘려(구간제한 ~9배) IC 가 0 으로 감쇠한다.
    # 섀도는 점수화된 전 후보를 담아 선택편향 없는 IC 를 준다(lib/rank_shadow.py). 없으면 생략.
    if surface in ("kr_mock", "us_mock"):
        try:
            _sl = Ledger(f"{surface}_shadow")
            ssnap = evolution.snapshot(_sl.training_set())
            if ssnap.get("n"):
                sbits = [f"섀도(전 후보) {ssnap['n']}건"]
                if ssnap.get("realized_ic") is not None:
                    sbits.append(f"IC {ssnap['realized_ic']:+.3f}")
                if ssnap.get("ic_ci"):
                    sbits.append(f"95%CI [{ssnap['ic_ci'][0]:+.2f},{ssnap['ic_ci'][1]:+.2f}]")
                lines.append("  🔬 " + " · ".join(sbits))
                # 축별 IC — 미검증 축(ranker/fund/signal/conf = 가중치 0.65)이 실제로
                # 예측력이 있는지 직접 측정. 유의(CI 가 0 미포함)한 축만 표기.
                axes = evolution.axis_ic(_sl.training_set())
                sig_axes = [(k, a) for k, a in axes.items()
                            if a.get("ci") and not (a["ci"][0] <= 0 <= a["ci"][1])]
                sig_axes.sort(key=lambda kv: -abs(kv[1]["ic"]))
                if sig_axes:
                    lines.append("  📐 유의 축: " + " · ".join(
                        f"{k} {a['ic']:+.2f}" for k, a in sig_axes[:4]))
                elif axes:
                    lines.append(f"  📐 유의 축 없음 ({len(axes)}축 측정 — 전부 CI 에 0 포함)")
            elif _sl.read_decisions():
                lines.append(f"  🔬 섀도 적재 {len(_sl.read_decisions())}건 (성숙 대기 — 20거래일)")
        except Exception:
            pass

    if surface in ("kr_mock", "us_mock"):
        _ol = _offline_verdict_line(surface)
        if _ol:
            lines.append(_ol)

    series = [s for s in ev["series"] if s.get("excess") is not None]
    if len(series) >= 2:
        lines.append(f"  추세 {fmt.spark([s['excess'] for s in series])} ({len(series)}주 OOS)")
    if ev["adoptions"]:
        last = ev["adoptions"][-1]
        lines.append(f"  최근 채택 {last.get('date', '')} · 총 {len(ev['adoptions'])}회")
    elif ev["n_runs"]:
        lines.append(f"  채택 0회 / 학습 {ev['n_runs']}주 (챌린저 미돌파)")
    return lines


def build_evolve_report(html: bool = False) -> str:
    """/evolve 본문 — KR+US 진화 통합. read-only·표시형."""
    import fmt
    _B = fmt.b if html else (lambda x: x)
    lines = [f"🧬 {_B('모의 자기개선 진화')}", fmt.sep()]
    lines += _surface_block("kr_mock", "🇰🇷", "국내(KR)", html)
    lines.append(fmt.sep())
    lines += _surface_block("us_mock", "🇺🇸", "미국(US)", html)
    lines.append(fmt.sep())
    lines += _surface_block("kr_intraday", "🕐", "단기 KR", html)
    lines.append(fmt.sep())
    lines += _surface_block("us_intraday", "🕐", "단기 US", html)
    lines.append(fmt.sep())
    lines.append("결정→순비용 보상→주간 OOS 재학습→챔피언·챌린저 (자기개선 루프)")
    lines.append("⚠️ 표시·모의 정책 · 실거래 미반영 · 무엣지면 정직 공개")
    return "\n".join(lines)
