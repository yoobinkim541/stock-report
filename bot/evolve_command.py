"""bot/evolve_command.py — /evolve: 모의 자기개선 "진화" 통합 리포트 (KR+US 한 커맨드).

주간 학습 이력(evolution.read_learning) + 라이브 원장 스냅샷(Ledger.training_set → 순비용 IC·
적중률·누적엣지)을 합쳐 **정직한 진화 verdict**를 낸다. owner 전용·표시형·read-only.
정직: 표본 부족이면 "콜드스타트", 충분해도 순비용 IC≈0 이면 "무엣지" 공개(과대주장 0).
"""
from __future__ import annotations


def _surface_block(surface: str, flag: str, name: str, html: bool) -> list[str]:
    import fmt
    from ml.adaptive import Ledger, evolution
    _B = fmt.b if html else (lambda x: x)
    try:
        rows = Ledger(surface).training_set()
    except Exception:
        rows = []
    ev = evolution.evolution_summary(surface, rows)
    snap, v = ev["snapshot"], ev["verdict"]

    lines = [f"{flag} {_B(name)} — {v['emoji']} {_B(v['label'])}", f"  {v['note']}"]

    bits = [f"성숙 {snap.get('n', 0)}건"]
    if snap.get("realized_ic") is not None:
        bits.append(f"순비용 IC {snap['realized_ic']:+.3f}")
    if snap.get("buy_hit") is not None:
        bits.append(f"적중 {snap['buy_hit']:.0f}%")
    if snap.get("cum_net_excess") is not None:
        bits.append(f"누적 {snap['cum_net_excess'] * 100:+.2f}%")
    lines.append("  " + " · ".join(bits))

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
    lines.append("결정→순비용 보상→주간 OOS 재학습→챔피언·챌린저 (자기개선 루프)")
    lines.append("⚠️ 표시·모의 정책 · 실거래 미반영 · 무엣지면 정직 공개")
    return "\n".join(lines)
