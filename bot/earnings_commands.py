"""bot/earnings_commands.py — /earnings 실적·밸류에이션·컨센서스 커맨드 (§G6).

  /earnings           포트폴리오 다가오는 실적 캘린더 + 직전 서프라이즈·밸류에이션 요약
  /earnings TICKER    단일 종목 상세: 밸류에이션·과거 서프라이즈·컨센서스·PEAD 반응 (한국: 005930.KS)

owner 전용(_GUEST_COMMANDS 미포함). 정보 제공형 — 예측·처방 아님. KR(.KS)=열화모드(밸류에이션만).
"""
from __future__ import annotations

import logging

import fmt

logger = logging.getLogger(__name__)


def _overview(send, chat_id: str) -> None:
    from providers import earnings_data as ed
    try:
        from portfolio_universe import load_portfolio_tickers
        tickers = load_portfolio_tickers()
    except Exception:
        tickers = []
    rows = []
    for t in tickers:
        try:
            rows.append(ed.summary(t))
        except Exception:
            continue

    def _key(s):
        d = (s.get("next_earnings") or {}).get("days_until")
        return d if isinstance(d, (int, float)) and d >= 0 else 10 ** 6

    rows.sort(key=_key)
    lines = [fmt.esc("📅 실적 캘린더 & 밸류에이션 (포트폴리오)"), fmt.SEP]   # HTML: & 이스케이프
    for s in rows:
        t = s.get("ticker", "?")
        v = s.get("valuation", {}) or {}
        n = s.get("next_earnings", {}) or {}
        last = s.get("last_surprise") or {}
        nd, du = n.get("date"), n.get("days_until")
        cal = f"{nd} (D-{du})" if nd and isinstance(du, (int, float)) and du >= 0 else (nd or "미정")
        per = f"PER {v['per']:.1f}x" if v.get("per") is not None else "PER —"
        dy = f"· 배당 {v['div_yield'] * 100:.2f}%" if v.get("div_yield") is not None else ""
        surp = f"· 직전 {last['surprise_pct']:+.1f}%" if last.get("surprise_pct") is not None else ""
        lines.append(f"• {fmt.b(fmt.name(t))} — {cal} | {per} {dy} {surp}".rstrip())
    lines.append("")
    lines.append("상세: /earnings TICKER")
    send(chat_id, "\n".join(lines))


def _detail(send, chat_id: str, ticker: str) -> None:
    from providers import earnings_data as ed
    from reports import earnings_reaction as er
    s = ed.summary(ticker)
    v = s.get("valuation", {}) or {}
    c = s.get("consensus", {}) or {}
    n = s.get("next_earnings", {}) or {}
    lines = [f"📊 {fmt.b(fmt.name(ticker))} 실적·밸류에이션", fmt.SEP]

    # 밸류 / 수익 2줄 분할 (6약어 한 줄 → 모바일 줄바꿈 방지)
    val_parts = []
    for label, key in [("PER", "per"), ("fwdPER", "forward_pe"), ("PBR", "pbr"), ("PSR", "psr")]:
        if v.get(key) is not None:
            val_parts.append(f"{label} {v[key]:.1f}x")
    prof_parts = []
    if v.get("roe") is not None:
        prof_parts.append(f"ROE {v['roe'] * 100:.1f}%")
    if v.get("eps_ttm") is not None:
        prof_parts.append(f"EPS {v['eps_ttm']:.2f}")
    lines.append("밸류  " + (" · ".join(val_parts) if val_parts else "데이터 없음"))
    if prof_parts:
        lines.append("수익  " + " · ".join(prof_parts))

    if v.get("div_yield") is not None:
        g = []
        if v.get("div_growth_1y") is not None:
            g.append(f"1y {v['div_growth_1y'] * 100:+.0f}%")
        if v.get("div_growth_3y") is not None:
            g.append(f"3y {v['div_growth_3y'] * 100:+.0f}%/yr")
        lines.append(f"배당: {v['div_yield'] * 100:.2f}%" + (f" (성장 {', '.join(g)})" if g else ""))

    if n.get("date"):
        du = n.get("days_until")
        lines.append(f"다음 실적: {n['date']}" + (f" (D-{du})" if isinstance(du, (int, float)) and du >= 0 else ""))

    if not s.get("degraded"):
        cp = []
        if c.get("eps_fwd_avg") is not None:
            cp.append(f"포워드 EPS {c['eps_fwd_avg']:.2f}({int(c['n_analysts']) if c.get('n_analysts') else '?'}명)")
        if c.get("revision_momentum") is not None:
            cp.append(f"리비전 모멘텀 {fmt.signed(c['revision_momentum'], 2)}")
        if c.get("target_upside_pct") is not None:
            cp.append(f"목표가 {c['target_upside_pct']:+.0f}%")
        if cp:
            lines.append("컨센서스: " + " · ".join(cp))
    else:
        lines.append("컨센서스: (KR 무료 데이터 한계 — 밸류에이션만)")

    try:
        hist = ed.earnings_history(ticker, limit=4)
    except Exception:
        hist = []
    if hist:
        lines.append("최근 서프라이즈:")
        for h in hist:
            sp = f"{h['surprise_pct']:+.1f}%" if h.get("surprise_pct") is not None else "—"
            lines.append(f"  {h['date']}: {sp} (실제 {h.get('eps_actual')} / 추정 {h.get('eps_est')})")

    try:
        rsum = er.analyze(ticker).get("summary", {})
    except Exception:
        rsum = {}
    if rsum.get("n"):
        parts = [f"평균 ±{rsum['avg_abs_move_1d'] * 100:.1f}%"]
        if rsum.get("beat_up_rate") is not None:
            parts.append(f"beat→상승 {rsum['beat_up_rate'] * 100:.0f}%")
        if rsum.get("drift_persistence") is not None:
            parts.append(f"드리프트 지속성 {rsum['drift_persistence'] * 100:.0f}%")
        lines.append(f"실적후 반응(PEAD, {rsum['n']}회): " + " · ".join(parts))

    # 실험적 예측(모델 캐시 있을 때만 — US·정보형). 방향은 예측 불가라 확률·변동폭만.
    if not s.get("degraded") and n.get("date"):
        try:
            from ml import earnings_predictor as _g3
            from ml import earnings_move_predictor as _g4
            pb = _g3.predict_for_ticker(ticker)
            mv = _g4.predict_for_ticker(ticker, beat_prob=pb)
            pp = []
            if pb is not None:
                pp.append(f"beat 확률 {pb * 100:.0f}%")
            if mv and mv.get("expected_abs_move") is not None:
                pp.append(f"기대 변동폭 ±{mv['expected_abs_move'] * 100:.1f}%")
            if pp:
                lines.append("🔮 예측(실험적): " + " · ".join(pp))
        except Exception:
            pass

    lines.append("")
    lines.append("※ 정보·실험적 예측 — 방향예측은 불가(변동폭·확률만). 투자 판단은 본인 책임.")
    send(chat_id, "\n".join(lines))


def cmd_earnings(chat_id: str, args: list, send_fn=None) -> None:
    """/earnings [TICKER] 핸들러."""
    if send_fn is None:
        from telegram_bot import send as send_fn
    _send = send_fn
    try:
        tok = (args[0].strip() if args and args[0].strip() else "")
        if tok:
            _detail(_send, chat_id, tok.upper())
        else:
            _overview(_send, chat_id)
    except Exception as e:
        logger.exception("cmd_earnings")
        _send(chat_id, f"⚠️ 실적 조회 실패: {e}")
