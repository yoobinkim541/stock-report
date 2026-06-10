#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portfolio advisor prompt and Codex-backed chat helper."""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent  # bot/ → 프로젝트 루트
ADVISOR_MODEL = os.environ.get("STOCK_ADVISOR_MODEL", "gpt-5.5")
EDITABLE_FILES = [
    "portfolio_snapshot.json",
    "price_alerts.json",
    "target_weights.json",
    "dca_weights.json",
    "leverage_state.json",
]


def _fmt(value, default="N/A"):
    return default if value is None else value


def _format_holdings(portfolio: dict) -> str:
    details = portfolio.get("holdings_detail") or []
    if details:
        lines = ["[개별 보유 종목]"]
        for h in details:
            ticker = _fmt(h.get("ticker"))
            name = _fmt(h.get("name"))
            shares = _fmt(h.get("shares"))
            value = h.get("value_usd")
            value_text = f"${value}" if value is not None else _fmt(h.get("value_krw"))
            ret = h.get("return_pct")
            ret_text = f", 수익률 {ret}%" if ret is not None else ""
            lines.append(f"- {ticker} — {name}: {shares}주, 평가 {value_text}{ret_text}")
        return "\n".join(lines) + "\n\n"

    holdings = portfolio.get("holdings") or {}
    if holdings:
        lines = ["[개별 보유 종목]"]
        for ticker, shares in holdings.items():
            lines.append(f"- {ticker}: {shares}주")
        return "\n".join(lines) + "\n\n"

    return ""


def build_ml_context() -> str:
    """ML 모델 현재 판단을 문자열로 반환 — 프롬프트에 주입용."""
    lines = ["[ML 모델 판단]"]
    try:
        import warnings; warnings.filterwarnings("ignore")

        # 1) 포트폴리오 종목 ML 점수
        from barbell_strategy import _ml_dca_blend, _DCA_WEIGHTS_DEFAULT, _ml_breadth_mult
        _, scores, breadth = _ml_dca_blend(_DCA_WEIGHTS_DEFAULT)
        if scores:
            sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            lines.append("- 포트폴리오 종목 ML 예측 (QQQ 초과수익, 내림차순):")
            for ticker, score in sorted_scores:
                lines.append(f"    {ticker}: {score*100:+.2f}%")
            ml_mult, ml_label = _ml_breadth_mult(breadth)
            lines.append(f"- 포트폴리오 평균 ML 강도: {breadth*100:+.2f}%  →  DCA {ml_mult}× 보정 ({ml_label or '중립'})")
    except Exception as e:
        lines.append(f"- 포트폴리오 ML 점수: 조회 실패 ({e})")

    try:
        # 2) NASDAQ100 상위/하위 랭킹
        from ml.ranker import load_ranker, rank_today
        result = load_ranker()
        if result:
            lines.append(f"- LightGBM 랭커 OOS IC: {result.oos_ic:+.3f}  ICIR: {result.oos_icir:.2f}")
        ranking = rank_today(mode="nasdaq100", top_n=5)
        if not ranking.empty:
            top5 = "  ".join(f"{r['ticker']}({r['score']*100:+.1f}%)"
                             for _, r in ranking.head(5).iterrows())
            lines.append(f"- NASDAQ100 상위 5: {top5}")
    except Exception as e:
        lines.append(f"- NASDAQ100 랭킹: 조회 실패 ({e})")

    try:
        # 3) ML 전략 최신 채택 판정
        from ml.reporting import _REAL_REPORT_CACHE
        cached = _REAL_REPORT_CACHE.get("QQQ_756")
        if cached:
            for line in cached.split("\n"):
                if "채택" in line and "판정" in line:
                    lines.append(f"- ML 전략 채택 판정: {line.strip()}")
                    break
    except Exception:
        pass

    try:
        # 4) Fear/Greed proxy 현재값
        from ml.data_pipeline import get_fg_proxy_score
        fg = get_fg_proxy_score()
        if fg >= 0:
            label = ("극도공포" if fg <= 20 else "공포" if fg <= 45 else
                     "중립" if fg <= 55 else "탐욕" if fg <= 75 else "극도탐욕")
            lines.append(f"- Fear/Greed Proxy: {fg:.1f}/100 ({label})")
    except Exception:
        pass

    return "\n".join(lines)


def build_advisor_prompt(question: str, market: dict) -> str:
    qqq = market.get("qqq", {})
    benchmarks = market.get("benchmarks", {})
    portfolio = market.get("portfolio", {})
    market_type = market.get("market_type")
    phase_key = market.get("phase_key")
    source_digest = market.get("source_digest") or "- 최근 24시간 수집 자료 없음"
    holdings_text = _format_holdings(portfolio)
    editable_text = ", ".join(EDITABLE_FILES)

    ml_context = build_ml_context()

    return (
        "너는 한국어로 답하는 포트폴리오 상담 보조자다.\n"
        "아래 시장/포트폴리오 핵심 데이터와 사용자의 질문에만 근거해 답하라.\n"
        "추정, 가정, 미확인 뉴스, 실제 데이터가 아닌 내용은 사용하지 말고 실제 데이터만 사용하라.\n"
        "투자 조언은 참고용이며 최종 투자 판단과 책임은 사용자에게 있음을 명시하라.\n"
        "사용자가 포트폴리오/알림/비중/레버리지 상태 파일 수정을 요청하면 파일 도구로 직접 반영하라.\n"
        f"편집 허용 파일: {editable_text}\n"
        "위 목록 밖의 파일, 코드 파일, .env, 토큰/시크릿 파일은 절대 수정하지 말라.\n"
        "\n"
        "[시장 데이터]\n"
        f"- 조회 시각: {_fmt(market.get('fetched_at'))}\n"
        f"- 시장/Phase: {_fmt(market_type)}/{_fmt(phase_key)}\n"
        f"- RSI: {_fmt(market.get('rsi'))}\n"
        f"- VIX: {_fmt(market.get('vix'))}\n"
        f"- 환율: {_fmt(market.get('exchange_rate'))}\n"
        f"- QQQ 현재가: {_fmt(qqq.get('current'))}\n"
        f"- QQQ 낙폭: {_fmt(qqq.get('drawdown_pct'))}%\n"
        "\n"
        "[벤치마크 성과]\n"
        f"- QQQ 현재가/YTD: {_fmt((benchmarks.get('QQQ') or {}).get('current'))} / {_fmt((benchmarks.get('QQQ') or {}).get('ytd_pct'))}%\n"
        f"- SPY 현재가/YTD: {_fmt((benchmarks.get('SPY') or {}).get('current'))} / {_fmt((benchmarks.get('SPY') or {}).get('ytd_pct'))}%\n"
        "- 포트폴리오 수익률과 YTD 벤치마크는 기준 기간이 다를 수 있으면 그 차이를 명시하라.\n"
        "\n"
        "[최근 신뢰 소스 요약]\n"
        f"{source_digest}\n"
        "위 자료의 출처가 명시된 항목만 근거로 사용하라.\n"
        "- FRED 국채(DGS5/10/20/30) 금리 곡선에서 경기침체·인플레이션 신호 분석 가능\n"
        "- WorldGovernmentBonds 5Y/10Y/20Y/30Y 만기별 금리: 장단기 스프레드 역전 여부 확인\n"
        "- Yahoo Finance 섹터별 ETF: 낙폭·모멘텀 비교로 순환 국면 판단\n"
        "- Fear & Greed Index: 극단 값에서 반전 가능성 평가\n"
        "\n"
        "[거시 평가 지침]\n"
        "- 위 수집 자료를 바탕으로 **거시적 관점**(통화정책, 채권시장, 글로벌 유동성)과 "
        "**미시적 관점**(개별 종목·섹터 펀더멘털, 포트폴리오 내 상대 강도)을 모두 포함하라.\n"
        "- **비관론 관점**: 경기 둔화, 채권 수익률 역전, 하이일드 스프레드 확대, 고용 냉각 신호가 있다면 짚어라.\n"
        "- **낙관론 관점**: 기술 혁신(AI, 반도체), 인플레 둔화, 소비/고용 강세, 금리 인하 기대가 있다면 짚어라.\n"
        "- **중립 관점**: 두 관점의 근거를 균형 있게 제시하고, 어느 쪽이 현재 데이터에 더 부합하는지 평가하라.\n"
        "- 결론을 낼 때는 반드시 '낙관론적 시나리오', '비관론적 시나리오', '중립 시나리오'를 각각 1~2문장으로 요약하라.\n"
        "\n"
        f"{ml_context}\n"
        "\n"
        "[포트폴리오 데이터]\n"
        f"- 총액(USD): {_fmt(portfolio.get('total_usd'))}\n"
        f"- SGOV(USD): {_fmt(portfolio.get('sgov_usd'))}\n"
        f"- QQQI(USD): {_fmt(portfolio.get('qqqi_usd'))}\n"
        "\n"
        f"{holdings_text}"
        "[답변 형식]\n"
        "1. 결론 (낙관/비관/중립 시나리오 포함)\n"
        "2. 근거 (거시 + 미시 각각)\n"
        "3. 실행 시 주의점\n"
        "\n"
        f"[사용자 질문]\n{question.strip()}"
    )


def _local_fallback(question: str, market: dict) -> str:
    """Hermes 호출 실패 시 ML 모델 데이터 기반 로컬 답변."""
    market_phase = f"{_fmt(market.get('market_type'))}/{_fmt(market.get('phase_key'))}"
    benchmarks   = market.get("benchmarks", {})
    portfolio    = market.get("portfolio", {})
    rsi, vix     = _fmt(market.get('rsi')), _fmt(market.get('vix'))
    qqq_ytd      = _fmt((benchmarks.get('QQQ') or {}).get('ytd_pct'))
    total_usd    = _fmt(portfolio.get('total_usd'))
    sgov_usd     = _fmt(portfolio.get('sgov_usd'))

    ml_ctx = build_ml_context()

    lines = [
        f"📊 로컬 ML 기반 분석 (AI 상담 서버 미응답)",
        f"",
        f"[질문] {question.strip()}",
        f"",
        f"[현재 시장]",
        f"- Phase: {market_phase}  |  RSI: {rsi}  |  VIX: {vix}",
        f"- QQQ YTD: {qqq_ytd}%  |  포트폴리오 총액: ${total_usd}  |  SGOV: ${sgov_usd}",
        f"",
        ml_ctx,
        f"",
        f"[ML 기반 시사점]",
    ]

    # ML 점수 기반 간단한 시사점 생성
    try:
        import warnings; warnings.filterwarnings("ignore")
        from barbell_strategy import _ml_dca_blend, _DCA_WEIGHTS_DEFAULT, _ml_breadth_mult
        _, scores, breadth = _ml_dca_blend(_DCA_WEIGHTS_DEFAULT)
        if scores:
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            top3 = [t for t, _ in top[:3]]
            bot3 = [t for t, _ in top[-3:]]
            _, lbl = _ml_breadth_mult(breadth)
            if breadth > 0.005:
                lines.append(f"- ML 모델이 포트폴리오 평균 강세 신호 ({breadth*100:+.2f}%) 감지 — 현 Phase DCA 배율 유지 적절")
            elif breadth < -0.005:
                lines.append(f"- ML 모델이 포트폴리오 평균 약세 신호 ({breadth*100:+.2f}%) 감지 — DCA 배율 보수적 조정 고려")
            else:
                lines.append(f"- ML 모델 중립 신호 ({breadth*100:+.2f}%) — Phase 규칙 그대로 유지")
            lines.append(f"- ML 선호 종목: {', '.join(top3)}  |  비선호: {', '.join(bot3)}")
    except Exception:
        lines.append("- ML 시사점: 데이터 조회 실패, Phase 규칙 기준으로 결정")

    lines += [
        "",
        "⚠️ 투자 조언은 참고용이며 최종 판단과 책임은 사용자에게 있습니다.",
    ]
    return "\n".join(lines)


def ask_portfolio_advisor(question: str, market: dict, runner=subprocess.run) -> str:
    prompt = build_advisor_prompt(question, market)
    cmd = [
        "hermes",
        "chat",
        "-q",
        prompt,
        "--provider",
        "openai-codex",
        "--model",
        ADVISOR_MODEL,
        "--toolsets",
        "file",
        "-Q",
    ]

    try:
        result = runner(cmd, capture_output=True, text=True, timeout=120, cwd=PROJECT_DIR)
    except Exception:
        return _local_fallback(question, market)

    if getattr(result, "returncode", 1) != 0:
        return _local_fallback(question, market)

    answer = (getattr(result, "stdout", "") or "").strip()
    if not answer:
        return _local_fallback(question, market)
    return answer
