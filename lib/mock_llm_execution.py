#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM shadow/guard layer for mock-trading rebalance orders.

The default is shadow measurement: ask for an order-level review, keep the
deterministic rebalance plan unchanged, and later score whether the suggested
intervention would have helped.  Actual order modification is only available
behind an explicit guarded_apply mode and only for risk-reducing buy changes.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
ALLOWED_ACTIONS = {"allow", "block", "reduce_half"}


def _env_bool(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).lower() not in ("0", "false", "no", "off")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


def horizons(default=(5, 20, 60)) -> list[int]:
    raw = os.getenv("MOCK_ORDER_LLM_HORIZONS", "")
    if not raw:
        return list(default)
    out = []
    for part in raw.split(","):
        try:
            value = int(part.strip())
        except ValueError:
            continue
        if value > 0 and value not in out:
            out.append(value)
    return out or list(default)


def report_horizon(default: int = 20) -> int:
    return _env_int("MOCK_ORDER_LLM_REPORT_HORIZON", default, 1)


def mode() -> str:
    raw = os.getenv("MOCK_ORDER_LLM_MODE", "shadow").lower()
    return raw if raw in ("shadow", "guarded_apply") else "shadow"


def _compact(text, limit=120) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _symbol(order: dict) -> str:
    return str(order.get("symbol") or order.get("code") or "").upper()


def _signal_symbol(signal: dict, market: str) -> str:
    if market == "KR":
        return str(signal.get("code") or signal.get("ticker") or "").replace(".KS", "").replace(".KQ", "").upper()
    return str(signal.get("ticker") or signal.get("symbol") or "").upper()


def _outcome_id(base_id: str, horizon: int) -> str:
    return f"{base_id}:h{horizon}"


def _base_decision_id(outcome_id: str) -> str:
    if ":h" in str(outcome_id):
        return str(outcome_id).rsplit(":h", 1)[0]
    return str(outcome_id)


def _position_payload(positions: dict, market: str) -> list[dict]:
    rows = []
    for key, p in (positions or {}).items():
        shares = int(p.get("shares", 0) or 0)
        if shares <= 0:
            continue
        rows.append({
            "symbol": str(key).upper(),
            "shares": shares,
            "value": p.get("value"),
            "cur_price": p.get("cur_price"),
            "return_pct": p.get("return_pct"),
            "name": p.get("name") if market == "KR" else None,
        })
    rows.sort(key=lambda x: -(float(x.get("value") or 0)))
    return rows[:10]


def build_order_review_payload(*, market: str, nav=None, cash=None, budget=None,
                               max_positions=None, orders=None, positions=None,
                               signals=None) -> dict:
    orders = orders or []
    signals = signals or []
    compact_signals = []
    for s in signals[:20]:
        compact_signals.append({
            "symbol": _signal_symbol(s, market),
            "ticker": s.get("ticker"),
            "code": s.get("code"),
            "price": s.get("price"),
            "policy_score": s.get("policy_score"),
            "score": s.get("score"),
            "action": s.get("action"),
            "rationale": s.get("rationale"),
        })
    return {
        "date": datetime.now(KST).strftime("%Y-%m-%d"),
        "market": market,
        "nav": nav,
        "cash": cash,
        "budget": budget,
        "max_positions": max_positions,
        "mode": mode(),
        "orders": [
            {**o, "symbol": _symbol(o)}
            for o in orders[:20]
            if _symbol(o)
        ],
        "positions": _position_payload(positions or {}, market),
        "signals": compact_signals,
        "allowed_order_actions": sorted(ALLOWED_ACTIONS),
    }


def build_order_review_prompt(payload: dict) -> str:
    return (
        "한국어 모의투자 주문 리밸런싱 검토자. 입력 JSON 안의 숫자·종목·주문만 사용한다.\n"
        "새 주문을 만들지 않는다. orders에 있는 주문별로만 allow/block/reduce_half 중 하나를 판단한다.\n"
        "목표는 성과 개선 가능성을 사후 측정하기 위한 shadow 리뷰다. 기본은 주문 변경이 아니라 기록이다.\n"
        "block/reduce_half는 과대회전, 급등추격, 현금부족, 낮은 확신, 실적/이벤트 전 추가매수 같은 경우에만 쓴다.\n"
        "매도 주문을 막는 판단은 매우 보수적으로만 제안한다. 입력에 없는 뉴스/전망/숫자를 만들지 않는다.\n"
        "출력은 JSON object 하나만 반환한다. 형식:\n"
        "{\"reviews\":[{\"symbol\":\"MSFT\",\"order_side\":\"buy\",\"order_action\":\"allow\","
        "\"reason\":\"정책점수 상위와 현금 여유 확인\",\"confidence\":70}]}\n\n"
        "입력 JSON:\n"
        "<<<DATA_START>>>\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}\n"
        "<<<DATA_END>>>"
    )


def _extract_json_object(text: str) -> dict:
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start < 0 or end < start:
        raise ValueError("no json object found")
    return json.loads(text[start:end + 1])


def validate_reviews(raw: dict, allowed_symbols: set[str]) -> dict[str, dict]:
    if not isinstance(raw, dict):
        raise ValueError("output must be object")
    reviews = raw.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError("reviews must be list")
    out = {}
    for item in reviews:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        if symbol not in allowed_symbols:
            raise ValueError(f"unknown symbol: {symbol}")
        action = item.get("order_action")
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"invalid order_action for {symbol}: {action}")
        side = item.get("order_side")
        if side not in ("buy", "sell"):
            raise ValueError(f"invalid order_side for {symbol}: {side}")
        try:
            confidence = int(max(0, min(100, float(item.get("confidence", 50)))))
        except (TypeError, ValueError):
            confidence = 50
        out[symbol] = {
            "symbol": symbol,
            "order_side": side,
            "order_action": action,
            "reason": _compact(item.get("reason"), 100),
            "confidence": confidence,
        }
    return out


def run_order_review(payload: dict, *, runner=subprocess.run) -> tuple[dict[str, dict], str]:
    if not _env_bool("MOCK_ORDER_LLM_ENABLED", "1"):
        return {}, "disabled"
    if not payload.get("orders"):
        return {}, "no orders"
    prompt = build_order_review_prompt(payload)
    cmd = [
        "hermes",
        "chat",
        "-q",
        prompt,
        "--provider",
        os.getenv("MOCK_ORDER_LLM_PROVIDER",
                  os.getenv("MOCK_REPORT_LLM_PROVIDER",
                            os.getenv("INVESTMENT_REPORT_LLM_PROVIDER", "openai-codex"))),
        "--model",
        os.getenv("MOCK_ORDER_LLM_MODEL",
                  os.getenv("MOCK_REPORT_LLM_MODEL",
                            os.getenv("INVESTMENT_REPORT_LLM_MODEL", "gpt-5-mini"))),
        "-Q",
    ]
    timeout = _env_int("MOCK_ORDER_LLM_TIMEOUT", 75, 20)
    try:
        result = runner(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return {}, f"call failed: {_compact(exc, 80)}"
    if getattr(result, "returncode", 1) != 0:
        return {}, f"call failed: {_compact(getattr(result, 'stderr', 'non-zero exit'), 80)}"
    try:
        allowed = {_symbol(o) for o in payload.get("orders", []) if _symbol(o)}
        return validate_reviews(_extract_json_object(getattr(result, "stdout", "") or ""), allowed), "ok"
    except Exception as exc:
        return {}, f"guard rejected: {_compact(exc, 80)}"


def apply_reviews(plan: list[dict], reviews: dict[str, dict],
                  *, apply_mode: str | None = None) -> tuple[list[dict], list[dict]]:
    apply_mode = apply_mode or mode()
    if apply_mode != "guarded_apply":
        return list(plan), []
    new_plan = []
    applied = []
    for order in plan:
        symbol = _symbol(order)
        review = reviews.get(symbol)
        action = (review or {}).get("order_action")
        # Guarded apply is intentionally narrow: only reduce/block buy orders.
        if order.get("side") == "buy" and action == "block":
            applied.append({**order, "symbol": symbol, "llm_applied": "block",
                            "llm_reason": review.get("reason")})
            continue
        if order.get("side") == "buy" and action == "reduce_half":
            qty = int(order.get("qty", 0) or 0) // 2
            applied.append({**order, "symbol": symbol, "llm_applied": "reduce_half",
                            "llm_reason": review.get("reason"), "new_qty": qty})
            if qty > 0:
                new_plan.append({**order, "qty": qty, "reason": f"{order.get('reason', '')}+LLM half"})
            continue
        new_plan.append(order)
    return new_plan, applied


def log_shadow_reviews(ledger, *, market: str, date: str, plan: list[dict],
                       reviews: dict[str, dict], signals_by: dict[str, dict],
                       applied_mode: str = "shadow") -> int:
    added = 0
    for order in plan:
        symbol = _symbol(order)
        review = reviews.get(symbol)
        if not review or review.get("order_action") == "allow":
            continue
        sig = signals_by.get(symbol, {})
        ticker = sig.get("ticker") or (f"{symbol}.KS" if market == "KR" else symbol)
        rec = {
            "id": f"{date}:{symbol}:{order.get('side')}:{review.get('order_action')}",
            "date": date,
            "ticker": ticker,
            "symbol": symbol,
            "code": symbol if market == "KR" else None,
            "market": market,
            "surface": f"{market.lower()}_mock_llm_shadow",
            "shadow": True,
            "mode": applied_mode,
            "order_side": order.get("side"),
            "qty": order.get("qty"),
            "rule_reason": order.get("reason"),
            "llm_action": review.get("order_action"),
            "llm_reason": review.get("reason"),
            "llm_confidence": review.get("confidence"),
            "policy_score": sig.get("policy_score"),
            "score": sig.get("score"),
            "features": sig.get("features"),
            "rationale": sig.get("rationale"),
            "ok": True,
        }
        before = len(ledger.read_decisions())
        ledger.log_decision(rec)
        added += 1 if len(ledger.read_decisions()) > before else 0
    return added


def _delta_for_shadow(*, market: str, order_side: str, llm_action: str, gross: float) -> float:
    from ml.adaptive import costs
    factor = 0.5 if llm_action == "reduce_half" else 1.0
    if order_side == "buy":
        net_rule_buy = gross - costs.round_trip_frac(market)
        return -net_rule_buy * factor
    if order_side == "sell":
        return gross * factor
    return 0.0


def shadow_training_set(ledger) -> list[dict]:
    decisions = {d.get("id"): d for d in ledger.read_decisions() if d.get("id")}
    rows = []
    for outcome in ledger.read_outcomes():
        base_id = outcome.get("base_decision_id") or _base_decision_id(outcome.get("decision_id", ""))
        decision = decisions.get(base_id)
        if decision:
            rows.append({**decision, **outcome, "base_decision_id": base_id})
    return rows


def pending_shadow_count(ledger, *, horizons_: list[int] | None = None) -> int:
    hs = horizons_ or horizons()
    done = {o.get("decision_id") for o in ledger.read_outcomes()}
    count = 0
    for d in ledger.read_decisions():
        base_id = d.get("id")
        if not base_id:
            continue
        for h in hs:
            if _outcome_id(base_id, h) not in done:
                count += 1
    return count


def backfill_shadow_outcomes(ledger, *, market: str, price_fn,
                             horizon: int | None = None,
                             horizons_: list[int] | None = None) -> int:
    hs = [horizon] if horizon else (horizons_ or horizons())
    done = {o.get("decision_id") for o in ledger.read_outcomes()}
    added = 0
    for d in ledger.read_decisions():
        action = d.get("llm_action")
        if action not in ("block", "reduce_half"):
            continue
        base_id = d.get("id")
        if not base_id:
            continue
        for h in hs:
            decision_id = _outcome_id(base_id, h)
            if decision_id in done:
                continue
            res = price_fn(d.get("ticker", ""), d.get("date", ""), h)
            if res is None:
                continue
            stock_ret, index_ret, stock_mdd, idx_mdd = res
            gross = stock_ret - index_ret
            delta = _delta_for_shadow(
                market=market,
                order_side=d.get("order_side"),
                llm_action=action,
                gross=gross,
            )
            ledger.log_outcome({
                "decision_id": decision_id,
                "base_decision_id": base_id,
                "horizon": h,
                "matured_at": datetime.now(KST).strftime("%Y-%m-%d"),
                "stock_ret": round(stock_ret, 5),
                "index_ret": round(index_ret, 5),
                "gross_excess": round(gross, 5),
                "llm_delta_excess": round(delta, 5),
                "fwd_mdd": round(stock_mdd, 5),
                "idx_fwd_mdd": round(idx_mdd, 5),
                "would_help": bool(delta > 0),
                "correct": bool(delta > 0),
                "success": bool(delta > 0),
            })
            done.add(decision_id)
            added += 1
    return added


def summarize_shadow(rows: list[dict], *, horizon: int | None = None) -> dict:
    if horizon is not None:
        rows = [r for r in rows if r.get("horizon") == horizon]
    matured = [r for r in rows if r.get("llm_delta_excess") is not None]
    n = len(matured)
    if not n:
        return {"n": 0, "horizon": horizon, "hit_rate": None, "avg_delta": None, "by_action": {}}
    hit = sum(1 for r in matured if r.get("would_help"))
    avg = sum(float(r.get("llm_delta_excess") or 0) for r in matured) / n
    by_action = {}
    for r in matured:
        action = r.get("llm_action")
        bucket = by_action.setdefault(action, {"n": 0, "hit": 0, "avg_delta": 0.0})
        bucket["n"] += 1
        bucket["hit"] += 1 if r.get("would_help") else 0
        bucket["avg_delta"] += float(r.get("llm_delta_excess") or 0)
    for bucket in by_action.values():
        bucket["hit_rate"] = round(bucket["hit"] / bucket["n"] * 100.0, 1) if bucket["n"] else None
        bucket["avg_delta"] = round(bucket["avg_delta"] / bucket["n"] * 100.0, 2) if bucket["n"] else None
    return {
        "n": n,
        "horizon": horizon,
        "hit_rate": round(hit / n * 100.0, 1),
        "avg_delta": round(avg * 100.0, 2),
        "by_action": by_action,
    }


def summary_line(summary: dict) -> str:
    if not summary.get("n"):
        return "성숙 표본 없음 — shadow 데이터 축적 중"
    prefix = f"{summary.get('horizon')}D " if summary.get("horizon") else ""
    return (
        f"{prefix}성숙 {summary['n']}건 · 적중 {summary['hit_rate']}% · "
        f"rule-only 대비 평균 {summary['avg_delta']:+.2f}%p"
    )
