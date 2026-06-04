#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portfolio advisor prompt and Codex-backed chat helper."""

import subprocess


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


def build_advisor_prompt(question: str, market: dict) -> str:
    qqq = market.get("qqq", {})
    portfolio = market.get("portfolio", {})
    market_type = market.get("market_type")
    phase_key = market.get("phase_key")
    holdings_text = _format_holdings(portfolio)

    return (
        "너는 한국어로 답하는 포트폴리오 상담 보조자다.\n"
        "아래 시장/포트폴리오 핵심 데이터와 사용자의 질문에만 근거해 답하라.\n"
        "추정, 가정, 미확인 뉴스, 실제 데이터가 아닌 내용은 사용하지 말고 실제 데이터만 사용하라.\n"
        "투자 조언은 참고용이며 최종 투자 판단과 책임은 사용자에게 있음을 명시하라.\n"
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
        "[포트폴리오 데이터]\n"
        f"- 총액(USD): {_fmt(portfolio.get('total_usd'))}\n"
        f"- SGOV(USD): {_fmt(portfolio.get('sgov_usd'))}\n"
        f"- QQQI(USD): {_fmt(portfolio.get('qqqi_usd'))}\n"
        "\n"
        f"{holdings_text}"
        "[답변 형식]\n"
        "1. 결론\n"
        "2. 근거\n"
        "3. 실행 시 주의점\n"
        "\n"
        f"[사용자 질문]\n{question.strip()}"
    )


def _local_fallback(question: str, market: dict) -> str:
    market_phase = f"{_fmt(market.get('market_type'))}/{_fmt(market.get('phase_key'))}"
    portfolio = market.get("portfolio", {})
    return (
        "Codex 5.5 상담 호출 실패로 로컬 안전 요약을 제공합니다.\n"
        f"- 질문: {question.strip()}\n"
        f"- 현재 시장/Phase: {market_phase}\n"
        f"- RSI: {_fmt(market.get('rsi'))}, VIX: {_fmt(market.get('vix'))}\n"
        f"- 포트폴리오 총액: ${_fmt(portfolio.get('total_usd'))}\n"
        f"- SGOV 실탄: ${_fmt(portfolio.get('sgov_usd'))}\n"
        "실제 데이터만 기준으로 보면, 추가 매수·매도 판단은 현재 Phase 규칙과 현금 비중을 우선 확인해 보수적으로 결정하세요.\n"
        "투자 조언은 참고용이며 최종 판단과 책임은 사용자에게 있습니다."
    )


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
        "gpt-5.5",
        "-Q",
    ]

    try:
        result = runner(cmd, capture_output=True, text=True, timeout=120)
    except Exception:
        return _local_fallback(question, market)

    if getattr(result, "returncode", 1) != 0:
        return _local_fallback(question, market)

    answer = (getattr(result, "stdout", "") or "").strip()
    if not answer:
        return _local_fallback(question, market)
    return answer
