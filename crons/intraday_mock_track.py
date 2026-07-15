#!/usr/bin/env python3
"""intraday_mock_track.py — 단기(1분봉) 모의 트레이딩 엔진 (매 1분 크론).

흐름(열린 시장만): ⓪유니버스 갱신 → ①롤오버·orphan 수리 → ②bar·호가 적재 →
③일손실 halt → ④청산(진입보다 항상 먼저) → ⑤진입(축 점수+하드가드).

규율 (docs/intraday-mock-trading-design.md):
  - **shadow 기본**(INTRADAY_SHADOW_ONLY=true): 가상체결(best 호가+보수 페널티)만
    원장·trade_events 기록. live 도 모의 어댑터(kiwoom_mock·kis_mock)만 — 실계좌 주문 경로 0.
  - 원장 id 는 f"{date}:{ticker}:{HHMMSS}" 명시(하루 다회 트레이드 — 기본 date:ticker 멱등 충돌 방지).
  - 청산 즉시 net-of-cost 보상 log_outcome (fwd_excess = 실현 net R — 주간 학습 원천).
  - state(~/.cache/intraday_mock_state.json)가 포지션의 유일 권위 — 손상 시 orphan 수리로 원장 정합 복구.

크론: * * * * * flock -n /tmp/intraday_mock_track.lock uv run python crons/intraday_mock_track.py
     INTRADAY_MOCK_ENABLED=false(기본) 면 즉시 no-op.
     US 레버리지 단기: 기초자산 신호(QQQ/NVDA 등) → 레버리지 ETF(TQQQ/NVDL 등) 가상체결.
     수량은 일손실 한도에서 남은 예산 안으로 자동 축소한다.
수동 검증: --dry-run (쓰기 0·판단 stdout — 크론과 동시 실행 안전).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STATE_PATH = os.path.expanduser("~/.cache/intraday_mock_state.json")
_LEDGER_BASE = None          # 테스트 주입용 (None = 기본 ~/reports/ml-data)

_TZ = {"KR": ZoneInfo("Asia/Seoul"), "US": ZoneInfo("America/New_York")}
_OPEN_MIN = {"KR": 9 * 60, "US": 9 * 60 + 30}
_CLOSE_MIN = {"KR": 15 * 60 + 30, "US": 16 * 60}
_SEED_ENV = {"KR": ("KIWOOM_MOCK_SEED", 10_000_000.0), "US": ("KOREA_MOCK_SEED", 100_000.0)}
_DEFAULT_MAX_CONCURRENT = 0  # 0 = 제한 없음. 남은 일손실 예산이 실제 제한자.


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _cfg_market_value(cfg: dict, key: str, mk: str | None, default):
    val = cfg.get(key, default)
    if isinstance(val, dict):
        market = (mk or "").upper()
        return val.get(market, val.get(market.lower(), default))
    return val


def load_cfg() -> dict:
    try:
        from providers import intraday_universe
        lev_enabled = intraday_universe.leverage_enabled()
        lev_map = intraday_universe.leverage_map()
    except Exception:
        lev_enabled, lev_map = False, {}
    spread_soft_kr = _env_f("INTRADAY_MAX_SPREAD_BPS_KR", 25.0)
    spread_soft_us = _env_f("INTRADAY_MAX_SPREAD_BPS_US", 5.0)
    spread_hard_kr = _env_f("INTRADAY_HARD_SPREAD_BPS_KR", spread_soft_kr)
    spread_hard_us = _env_f("INTRADAY_HARD_SPREAD_BPS_US", 50.0)
    max_concurrent_default = int(_env_f("INTRADAY_MAX_CONCURRENT_POS", _DEFAULT_MAX_CONCURRENT))
    return {
        "enabled": os.getenv("INTRADAY_MOCK_ENABLED", "false").lower() == "true",
        "shadow": os.getenv("INTRADAY_SHADOW_ONLY", "true").lower() == "true",
        "markets": [m.strip().upper() for m in os.getenv("INTRADAY_MARKETS", "kr,us").split(",") if m.strip()],
        "sleeve_frac": _env_f("INTRADAY_SLEEVE_FRAC", 0.10),
        "risk_per_trade": _env_f("INTRADAY_RISK_PER_TRADE", 0.005),
        "max_trades": int(_env_f("INTRADAY_MAX_TRADES_DAY", 0)),
        "cooldown_min": int(_env_f("INTRADAY_COOLDOWN_MIN", 0)),
        "stop_friction_mult": _env_f("INTRADAY_STOP_FRICTION_MULT", 3.0),
        "min_notional": {"KR": _env_f("INTRADAY_MIN_NOTIONAL_KRW", 100000),
                         "US": _env_f("INTRADAY_MIN_NOTIONAL_USD", 150)},
        "daily_loss_halt": _env_f("INTRADAY_DAILY_LOSS_HALT", 0.015),
        "spread_cap": {"KR": spread_soft_kr, "US": spread_soft_us},
        "spread_hard_cap": {"KR": max(spread_soft_kr, spread_hard_kr),
                            "US": max(spread_soft_us, spread_hard_us)},
        "flat_buffer_min": int(_env_f("INTRADAY_FLAT_BUFFER_MIN", 15)),
        "entry_cutoff_min": int(_env_f("INTRADAY_ENTRY_CUTOFF_MIN", 30)),
        # 개장 첫 N분은 진입 보류 — 개장 동시호가 직후 스프레드가 구조적으로 넓어
        # 하필 이 순간에 몰리는 진입기준 통과 신호가 스프레드 가드에 거의 매번
        # 막히던 문제 방어(2026-07-15 실측). US 는 개장 변동성이 커 기본 더 김.
        "open_buffer_min": {"KR": int(_env_f("INTRADAY_OPEN_BUFFER_MIN_KR",
                                             _env_f("INTRADAY_OPEN_BUFFER_MIN", 2))),
                            "US": int(_env_f("INTRADAY_OPEN_BUFFER_MIN_US",
                                             _env_f("INTRADAY_OPEN_BUFFER_MIN", 3)))},
        "stale_flat_min": int(_env_f("INTRADAY_STALE_FLAT_MIN", 10)),
        "orb_minutes": int(_env_f("INTRADAY_ORB_MINUTES", 15)),
        "explore_enabled": os.getenv("INTRADAY_EXPLORE_ENABLED", "true").lower() == "true",
        "explore_entry": {
            "KR": _env_f("INTRADAY_EXPLORE_ENTRY_KR", _env_f("INTRADAY_EXPLORE_ENTRY", 0.40)),
            "US": _env_f("INTRADAY_EXPLORE_ENTRY_US", _env_f("INTRADAY_EXPLORE_ENTRY", 0.48)),
        },
        "explore_risk_mult": {
            "KR": _env_f("INTRADAY_EXPLORE_RISK_MULT_KR", _env_f("INTRADAY_EXPLORE_RISK_MULT", 0.35)),
            "US": _env_f("INTRADAY_EXPLORE_RISK_MULT_US", _env_f("INTRADAY_EXPLORE_RISK_MULT", 0.50)),
        },
        "max_concurrent": {
            "KR": int(_env_f("INTRADAY_MAX_CONCURRENT_POS_KR", max_concurrent_default)),
            "US": int(_env_f("INTRADAY_MAX_CONCURRENT_POS_US", max_concurrent_default)),
        },
        "leverage_enabled": lev_enabled,
        "leverage_map": lev_map,
    }


# ── state (tmp→rename 원자적 — 포지션 유일 권위) ─────────────────────────────

def _blank_state() -> dict:
    return {"session_date": {}, "positions": {}, "counters": {}, "halt": {},
            "cooldown_until": {}, "obi_hist": {}, "last_processed_bar": {},
            "profiles": {}, "last_run": None}


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
        base = _blank_state()
        base.update(st if isinstance(st, dict) else {})
        return base
    except FileNotFoundError:
        return _blank_state()
    except Exception as e:
        logger.warning("state 손상 — 빈 상태로 시작(orphan 수리가 원장 복구): %s", e)
        return _blank_state()


def save_state(state: dict) -> None:
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    try:
        import safe_io
        safe_io.atomic_write_json(STATE_PATH, state)
    except Exception as e:
        logger.error("state 저장 실패: %s", e)


# ── 시장 데이터 헬퍼 ──────────────────────────────────────────────────────────

def _now_local(mk: str) -> datetime:
    return datetime.now(_TZ[mk])


def _market_open(mk: str) -> bool:
    from ml.intraday_signal import is_kr_market_open, is_us_market_open
    return is_kr_market_open() if mk == "KR" else is_us_market_open()


def _rest_price(sym: str, mk: str) -> float | None:
    """신선도 실패 시 청산용 가격 폴백 — 실시간 캐시 → KIS REST → (US) 모의 시세."""
    try:
        from providers import realtime_quotes
        p = realtime_quotes.get_price(sym, max_age_s=300)
        if p:
            return p
    except Exception:
        pass
    try:
        from providers import kis_quote
        q = kis_quote.get_quote(sym, market=mk)
        if q and q.get("price"):
            return q["price"]
    except Exception:
        pass
    if mk == "US":
        try:
            import kis_mock
            return kis_mock.get_price(sym)
        except Exception:
            pass
    return None


def _news_events(now_epoch: float) -> list[dict]:
    """최근 이벤트 → axis_news 입력 정규화 [{symbols, epoch, direction, strength}].

    LLM 라벨(providers/news_labels JSONL) 있으면 방향·강도, 없으면 방향 미상(중립).
    """
    out = []
    labels_by_id: dict = {}
    try:
        from providers import news_labels
        for lab in news_labels.load_labels() or []:
            if lab.get("id"):
                labels_by_id[lab["id"]] = lab
    except Exception:
        pass
    try:
        from reports.source_collector import load_recent_events
        events = load_recent_events(hours=2) or []
    except Exception:
        return []
    from providers.intraday_bars import base_symbol
    for e in events:
        tickers = [base_symbol(t) for t in (e.get("tickers") or []) if t]
        if not tickers:
            continue
        try:
            ep = datetime.fromisoformat(str(e.get("published_at"))).timestamp()
        except (TypeError, ValueError):
            continue
        lab = labels_by_id.get(e.get("id")) or {}
        out.append({"symbols": tickers, "epoch": ep,
                    "direction": lab.get("direction"), "strength": lab.get("strength")})
    return out


# ── 체결 (shadow 가상 / live 모의 어댑터) ─────────────────────────────────────

def _fill(side: str, sym: str, qty: int, mk: str, *, shadow: bool,
          orderbook: dict | None, last_price: float | None,
          dry: bool = False) -> dict | None:
    """반환 {price, penalty_per_share, ok, mode} | None(가격 원천 전무·주문 실패)."""
    from ml import intraday_axes as ax
    bb = (orderbook or {}).get("best_bid")
    ba = (orderbook or {}).get("best_ask")
    vf = ax.virtual_fill(side, bb, ba, last_price, mk)
    if vf is None:
        return None
    px, penalty = vf
    if shadow or dry:
        return {"price": px, "penalty": penalty, "ok": True, "mode": "shadow"}
    try:
        if mk == "KR":
            import kiwoom_mock
            if not kiwoom_mock.is_enabled():
                return {"price": px, "penalty": penalty, "ok": True, "mode": "shadow"}
            r = kiwoom_mock.place_order(sym, qty, side, price=int(px))
        else:
            import kis_mock
            if not kis_mock.is_enabled():
                return {"price": px, "penalty": penalty, "ok": True, "mode": "shadow"}
            r = kis_mock.place_order(sym, qty, side, price=px)
    except Exception as e:
        logger.warning("모의 주문 예외 %s %s: %s", side, sym, e)
        return None
    if not r.get("ok"):
        logger.warning("모의 주문 거부 %s %s: %s", side, sym, r.get("msg"))
        return None
    return {"price": px, "penalty": penalty, "ok": True, "mode": "live"}


def _record_event(sym: str, mk: str, side: str, qty: int, px: float, *,
                  decision_id: str, direction: str, avg_price: float | None,
                  shadow: bool, note: str, dry: bool) -> None:
    """trade_events 원장 기록 → 대시보드 차트 ▲▼ 마커. event_id 결정론(재실행 멱등)."""
    if dry:
        return
    try:
        from lib import trade_events
        trade_events.record_trade(
            ticker=sym, side=side, qty=qty, price=px, avg_price=avg_price,
            account="shadow" if shadow else ("kiwoom_mock" if mk == "KR" else "kis_mock"),
            source="intraday_mock", market=mk,
            timestamp=datetime.now(_TZ[mk]).isoformat(timespec="seconds"),
            note=note[:140], event_id=f"intr-{decision_id}-{direction}")
    except Exception as e:
        logger.warning("trade_events 기록 실패(무시): %s", e)


# ── 심볼 점수 (축 조립) ───────────────────────────────────────────────────────

def _symbol_axes(sym: str, mk: str, df, feats, *, profile: dict, obi_samples: list,
                 news_events: list, now_epoch: float, orb_minutes: int) -> dict | None:
    """확정 분봉 + 호가·뉴스 → 축 dict. 데이터 부족 시 None."""
    from ml import intraday_axes as ax
    if df is None or getattr(df, "empty", True) or feats is None or getattr(feats, "empty", True):
        return None
    row = feats.iloc[-1]
    close = float(df["Close"].iloc[-1])
    # ORB — 프리마켓 봉 제외(US 스트림은 개장 전 틱도 옴 — 라이브 실증) 후,
    # 세션 첫 봉이 개장분일 때만 (장중 신규 편입 심볼은 결측)
    mins = df.index.hour * 60 + df.index.minute
    sess = df[mins >= _OPEN_MIN[mk]]
    orr = None
    if len(sess) >= orb_minutes:
        sess_first = sess.index[0].hour * 60 + sess.index[0].minute
        if sess_first <= _OPEN_MIN[mk] + 1:
            orr = ax.opening_range(sess, orb_minutes)
    hhmm = df.index[-1].strftime("%H:%M")
    vol = float(df["Volume"].iloc[-1])
    vz = ax.tod_vol_z(vol, hhmm, profile)
    if vz is None:
        vz = ax.vol_z_fallback([float(v) for v in df["Volume"]])   # 프로파일 콜드스타트 — 신뢰 강등
    try:
        impulse = float(row.get("mom_3")) * 100.0
    except (TypeError, ValueError):
        impulse = None
    axes = {
        "orb": ax.axis_orb(close, orr, vz),
        "vwap": ax.axis_vwap(list(feats["vwap_dev"]), list(df["Close"]), list(df["Open"])),
        "volspike": ax.axis_volspike(vz, impulse),
        "ofi": ax.axis_ofi(obi_samples) if mk == "KR" else None,
        "news": ax.axis_news(news_events, sym, now_epoch),
        **ax.axis_legacy(row.to_dict()),
    }
    er = ax.regime_er(list(df["Close"]))
    axes = ax.apply_regime(axes, ax.regime_multipliers(er))
    axes["_meta"] = {"vol_z_tod": vz, "regime_er": er, "close": close,
                     "atr": float(row.get("atr")) if row.get("atr") == row.get("atr") else None,
                     "bar_ts": df.index[-1].isoformat(), "epoch_min": int(df.index[-1].timestamp() // 60),
                     "symbol": sym}
    return axes


def _trade_symbol(signal_sym: str, mk: str, cfg: dict) -> str:
    if mk != "US" or not cfg.get("leverage_enabled"):
        return signal_sym
    return (cfg.get("leverage_map") or {}).get(signal_sym, signal_sym)


def _execution_axes(signal_axes: dict, trade_sym: str, trade_df, trade_feats) -> dict | None:
    """기초자산 점수축 + 체결 ETF 가격축. stop/size 는 체결 ETF ATR 기준."""
    sig_meta = signal_axes.get("_meta") or {}
    if trade_sym == sig_meta.get("symbol"):
        return signal_axes
    if trade_df is None or getattr(trade_df, "empty", True) or trade_feats is None or getattr(trade_feats, "empty", True):
        return None
    try:
        row = trade_feats.iloc[-1]
        close = float(trade_df["Close"].iloc[-1])
        atr = float(row.get("atr")) if row.get("atr") == row.get("atr") else None
        if not close or not atr or atr <= 0:
            return None
        out = {k: v for k, v in signal_axes.items() if k != "_meta"}
        out["_meta"] = {
            **sig_meta,
            "close": close,
            "atr": atr,
            "bar_ts": trade_df.index[-1].isoformat(),
            "epoch_min": int(trade_df.index[-1].timestamp() // 60),
            "signal_ticker": sig_meta.get("symbol"),
            "execution_ticker": trade_sym,
        }
        return out
    except Exception:
        return None


def _open_risk(state: dict, mk: str) -> float:
    total = 0.0
    for key, pos in (state.get("positions") or {}).items():
        if not str(key).startswith(f"{mk}:"):
            continue
        try:
            total += float(pos.get("risk_per_share") or 0) * int(pos.get("qty") or 0)
        except (TypeError, ValueError):
            continue
    return max(0.0, total)


def _remaining_loss_budget(state: dict, mk: str, sleeve: float, cfg: dict) -> float:
    """오늘 순손익이 -daily_loss_halt*sleeve 밑으로 내려가지 않도록 신규 위험을 제한."""
    c = state["counters"].setdefault(mk, {"trades": 0, "day_pnl": 0.0, "sleeve_pnl_cum": 0.0})
    limit = max(0.0, float(cfg.get("daily_loss_halt", 0.0)) * max(sleeve, 0.0))
    return max(0.0, limit + float(c.get("day_pnl") or 0.0) - _open_risk(state, mk))


def _entry_risk_mult(score: float, params: dict, cfg: dict, mk: str | None = None) -> tuple[float, str]:
    """정규 진입선 아래 탐색 구간은 작은 리스크로 표본을 쌓는다."""
    theta_entry = float(params.get("theta_entry", 0.55))
    if score >= theta_entry:
        return 1.0, "normal"
    if not cfg.get("explore_enabled", True):
        return 0.0, "skip"
    theta_explore = min(theta_entry, float(_cfg_market_value(cfg, "explore_entry", mk, 0.48)))
    if score < theta_explore:
        return 0.0, "skip"
    mult = max(0.0, min(1.0, float(_cfg_market_value(cfg, "explore_risk_mult", mk, 0.50))))
    return (mult, "explore") if mult > 0 else (0.0, "skip")


def _max_concurrent(mk: str, cfg: dict) -> int:
    return max(0, int(_cfg_market_value(cfg, "max_concurrent", mk, _DEFAULT_MAX_CONCURRENT)))


def _entry_spread_cap(price: float, mk: str, cfg: dict) -> float:
    """진입 차단에는 하드캡을 쓰고, 실제 스프레드는 마찰/수량 산식에 반영한다."""
    from ml import intraday_axes as ax
    caps = cfg.get("spread_hard_cap") or cfg.get("spread_cap") or {}
    return ax.spread_cap_bps(price, mk, float(caps.get(mk, 0.0)))


# ── 청산·진입 실행 ────────────────────────────────────────────────────────────

def _do_exit(state: dict, key: str, pos: dict, reason: str, ref_px: float, mk: str,
             cfg: dict, ledger, *, orderbook=None, dry=False, notes=None) -> bool:
    from ml.adaptive import costs
    sym, qty = pos["ticker"], int(pos["qty"])
    fill = _fill("sell", sym, qty, mk, shadow=bool(pos.get("shadow", True)),
                 orderbook=orderbook, last_price=ref_px, dry=dry)
    if fill is None:
        (notes if notes is not None else []).append(f"⚠️ {sym} 청산 실패({reason}) — 다음 분 재시도")
        return False
    # 손절/목표는 판정가가 권위 (호가가 더 유리해도 보수 유지)
    exit_px = min(fill["price"], ref_px) if reason in ("stop",) else \
        (ref_px if reason == "target" else fill["price"])
    entry_px = float(pos["entry_price"])
    gross = (exit_px - entry_px) * qty
    cost = (costs.order_cost(entry_px * qty, "buy", mk)
            + costs.order_cost(exit_px * qty, "sell", mk))
    penalty = (float(pos.get("penalty_entry") or 0) + fill["penalty"]) * qty
    net = gross - cost - penalty
    rps = max(float(pos.get("risk_per_share") or 0), 1e-9)
    realized_r = net / (rps * qty)
    now = datetime.now(_TZ[mk])
    holding_min = int((time.time() - float(pos.get("entry_epoch") or time.time())) / 60)
    if not dry:
        try:
            ledger.log_outcome({
                "decision_id": pos["decision_id"], "exit_ts": now.isoformat(timespec="seconds"),
                "exit_reason": reason, "entry_price": entry_px, "exit_price": round(exit_px, 4),
                "qty": qty, "holding_min": holding_min, "gross_pnl": round(gross, 2),
                "cost": round(cost, 2), "slippage_penalty": round(penalty, 2),
                "net_pnl": round(net, 2), "realized_r": round(realized_r, 4),
                "fwd_excess": round(realized_r, 4), "success": bool(net > 0),
                "date": now.strftime("%Y-%m-%d")})
        except Exception as e:
            logger.error("outcome 기록 실패 %s: %s", sym, e)
    _record_event(sym, mk, "sell", qty, exit_px, decision_id=pos["decision_id"],
                  direction="out", avg_price=entry_px, shadow=bool(pos.get("shadow", True)),
                  note=f"단기 {reason} R={realized_r:+.2f}"
                       + (" (shadow)" if pos.get("shadow", True) else ""), dry=dry)
    c = state["counters"].setdefault(mk, {"trades": 0, "day_pnl": 0.0, "sleeve_pnl_cum": 0.0})
    c["day_pnl"] = c.get("day_pnl", 0.0) + net
    c["sleeve_pnl_cum"] = c.get("sleeve_pnl_cum", 0.0) + net
    cooldown_min = int(cfg.get("cooldown_min") or 0)
    if net < 0 and cooldown_min > 0:                # 선택 옵션. 기본은 즉시 재진입 허용.
        state["cooldown_until"][key] = time.time() + cooldown_min * 60
    else:
        state["cooldown_until"].pop(key, None)
    state["positions"].pop(key, None)
    (notes if notes is not None else []).append(
        f"{'🟢' if net > 0 else '🔴'} {sym} 청산[{reason}] {qty}주 @ {exit_px:,.2f} "
        f"net {net:+,.0f} (R{realized_r:+.2f})")
    return True


def _do_entry(state: dict, sym: str, mk: str, axes: dict, score: float, params: dict,
              cfg: dict, sleeve: float, ledger, *, orderbook=None, loss_budget=None,
              risk_mult=1.0, entry_mode="normal", dry=False, notes=None) -> bool:
    from ml import intraday_axes as ax
    meta = axes["_meta"]
    price, atr = meta["close"], meta.get("atr")
    if not price or not atr or atr <= 0:
        return False
    ob_spread = None
    if orderbook and orderbook.get("best_bid") and orderbook.get("best_ask"):
        ob_spread = float(orderbook["best_ask"]) - float(orderbook["best_bid"])
    friction = ax.friction_per_share(price, mk, spread=ob_spread)
    # 스탑폭 하한 = 마찰×배수 — 1분 ATR 타이트 스탑(첫 트레이드 1분 스탑아웃)·마찰 지배 방어
    stop = ax.stop_with_floor(price, atr, float(params.get("stop_atr_mult", 1.2)),
                              friction, floor_mult=cfg["stop_friction_mult"])
    target = price + float(params.get("target_r", 2.0)) * (price - stop)
    risk_frac = cfg["risk_per_trade"] * max(0.0, float(risk_mult))
    qty = ax.position_size(sleeve, risk_frac, price, stop,
                           friction=friction, min_notional=cfg["min_notional"][mk],
                           loss_budget=loss_budget)
    if qty < 1:
        return False
    shadow = cfg["shadow"]
    fill = _fill("buy", sym, qty, mk, shadow=shadow, orderbook=orderbook,
                 last_price=price, dry=dry)
    if fill is None:
        return False
    now = datetime.now(_TZ[mk])
    did = f"{now.strftime('%Y-%m-%d')}:{sym}:{now.strftime('%H%M%S')}"
    feats_rec = {k: (round(v, 4) if isinstance(v, float) else v)
                 for k, v in axes.items() if k != "_meta"}
    feats_rec.update({"spread_bps": round(ax.spread_bps((orderbook or {}).get("best_bid"),
                                                        (orderbook or {}).get("best_ask")) or -1, 2),
                      "vol_z_tod": round(meta["vol_z_tod"], 2) if meta.get("vol_z_tod") is not None else None,
                      "regime_er": round(meta["regime_er"], 3) if meta.get("regime_er") is not None else None})
    if not dry:
        try:
            ledger.log_decision({
                "id": did, "date": now.strftime("%Y-%m-%d"), "ticker": sym,
                "side": "단기진입", "order_side": "buy", "qty": qty,
                "price": round(fill["price"], 4), "bar_ts": meta["bar_ts"],
                "score": round(score, 4), "features": feats_rec,
                "stop": round(stop, 4), "target": round(target, 4),
                "signal_ticker": meta.get("signal_ticker"),
                "execution_ticker": meta.get("execution_ticker") or sym,
                "loss_budget": round(float(loss_budget), 2) if loss_budget is not None else None,
                "entry_mode": entry_mode, "risk_mult": round(float(risk_mult), 4),
                "risk_frac": round(float(risk_frac), 6),
                "shadow": shadow, "ok": True})
        except Exception as e:
            logger.error("decision 기록 실패 %s: %s", sym, e)
            return False
    _record_event(sym, mk, "buy", qty, fill["price"], decision_id=did, direction="in",
                  avg_price=fill["price"], shadow=shadow,
                  note=f"단기 {'탐색 ' if entry_mode == 'explore' else ''}진입 score={score:.2f} stop={stop:,.0f}"
                       + (" (shadow)" if shadow else ""), dry=dry)
    state["positions"][f"{mk}:{sym}"] = {
        "decision_id": did, "ticker": sym, "market": mk, "qty": qty,
        "entry_price": fill["price"], "entry_epoch": time.time(),
        "entry_min": now.hour * 60 + now.minute, "stop": stop, "target": target,
        "risk_per_share": (price - stop) + friction, "shadow": shadow,
        "penalty_entry": fill["penalty"], "last_score": score,
        "signal_ticker": meta.get("signal_ticker"), "loss_budget_entry": loss_budget,
        "entry_mode": entry_mode, "risk_mult": risk_mult, "risk_frac": risk_frac}
    c = state["counters"].setdefault(mk, {"trades": 0, "day_pnl": 0.0, "sleeve_pnl_cum": 0.0})
    c["trades"] = c.get("trades", 0) + 1
    (notes if notes is not None else []).append(
        f"▶️ {sym} {'탐색 ' if entry_mode == 'explore' else ''}진입 {qty}주 @ {fill['price']:,.2f} score {score:.2f}"
        + (f" ({meta.get('signal_ticker')} 신호)" if meta.get("signal_ticker") and meta.get("signal_ticker") != sym else "")
        + f" (stop {stop:,.0f}·tgt {target:,.0f}){' [shadow]' if shadow else ''}")
    return True


def _flatten_all(state: dict, mk: str, reason: str, cfg: dict, ledger,
                 *, dry=False, notes=None) -> None:
    for key in [k for k in list(state["positions"]) if k.startswith(f"{mk}:")]:
        pos = state["positions"][key]
        px = _rest_price(pos["ticker"], mk) or float(pos["entry_price"])
        _do_exit(state, key, pos, reason, px, mk, cfg, ledger, dry=dry, notes=notes)


def _repair_orphans(state: dict, mk: str, ledger, *, dry=False) -> int:
    """state 에 없는 당일 pending 결정 = orphan → 현재가로 즉시 청산 기록 (원장 정합 복구)."""
    today = _now_local(mk).strftime("%Y-%m-%d")
    live_ids = {p.get("decision_id") for p in state["positions"].values()}
    n = 0
    for d in ledger.pending():
        if d.get("date") != today or d.get("id") in live_ids or d.get("ok") is False:
            continue
        if d.get("side") != "단기진입":
            continue
        px = _rest_price(d.get("ticker", ""), mk) or float(d.get("price") or 0)
        if not px or dry:
            continue
        entry_px = float(d.get("price") or px)
        qty = int(d.get("qty") or 0) or 1
        rps = max(entry_px - float(d.get("stop") or entry_px * 0.995), 1e-9)
        net = (px - entry_px) * qty          # 비용 미차감 — 수리 레코드는 보수적 참고치
        ledger.log_outcome({
            "decision_id": d["id"], "exit_ts": datetime.now(_TZ[mk]).isoformat(timespec="seconds"),
            "exit_reason": "orphan_repair", "entry_price": entry_px, "exit_price": px,
            "qty": qty, "holding_min": None, "gross_pnl": round(net, 2), "cost": 0.0,
            "slippage_penalty": 0.0, "net_pnl": round(net, 2),
            "realized_r": round(net / (rps * qty), 4), "fwd_excess": round(net / (rps * qty), 4),
            "success": bool(net > 0), "date": today})
        n += 1
    if n:
        logger.warning("[%s] orphan 결정 %d건 수리 (state 유실 — 현재가 청산 기록)", mk, n)
    return n


# ── 시장별 1회 실행 ───────────────────────────────────────────────────────────

def run_market(mk: str, state: dict, cfg: dict, *, dry: bool = False) -> list[str]:
    notes: list[str] = []
    from ml.adaptive import Ledger
    ledger = Ledger(f"{mk.lower()}_intraday", base_dir=_LEDGER_BASE)

    if not _market_open(mk):
        if any(k.startswith(f"{mk}:") for k in state["positions"]):
            _flatten_all(state, mk, "stale_flat", cfg, ledger, dry=dry, notes=notes)
        return notes

    now = _now_local(mk)
    now_min = now.hour * 60 + now.minute
    today = now.strftime("%Y-%m-%d")

    # ⓪ 유니버스 (보유 유지 히스테리시스)
    held = [p["ticker"] for k, p in state["positions"].items() if k.startswith(f"{mk}:")]
    try:
        from providers import intraday_universe
        universe = (intraday_universe.current_universe(mk) if dry
                    else intraday_universe.refresh(mk, keep=held))
    except Exception as e:
        logger.warning("[%s] 유니버스 실패 — 보유만 관리: %s", mk, e)
        universe = list(held)

    # ① 세션 롤오버 — 카운터 리셋·전일 잔존 청산
    if state["session_date"].get(mk) != today:
        if any(k.startswith(f"{mk}:") for k in state["positions"]):
            _flatten_all(state, mk, "stale_flat", cfg, ledger, dry=dry, notes=notes)
        state["session_date"][mk] = today
        state["counters"][mk] = {"trades": 0, "day_pnl": 0.0,
                                 "sleeve_pnl_cum": (state["counters"].get(mk) or {}).get("sleeve_pnl_cum", 0.0)}
        state["halt"][mk] = False
        state["cooldown_until"] = {k: v for k, v in state["cooldown_until"].items()
                                   if not k.startswith(f"{mk}:")}
        state["profiles"].pop(mk, None)
        state["obi_hist"] = {k: v for k, v in state["obi_hist"].items()
                             if k not in [f"{mk}:{s}" for s in universe + held]}
    _repair_orphans(state, mk, ledger, dry=dry)

    # ② 데이터 적재 — bar·호가·신선도·프로파일
    from providers import intraday_bars, realtime_quotes
    from ml.intraday_signal import compute_intraday_features
    lev_map = cfg.get("leverage_map") or {}
    mapped = [lev_map[s] for s in universe if mk == "US" and cfg.get("leverage_enabled") and s in lev_map]
    watch = list(dict.fromkeys(universe + mapped + held))
    bars, feats, obs, fresh = {}, {}, {}, {}
    hb_ok = False
    try:
        hb_ok = realtime_quotes.enabled() and (realtime_quotes.heartbeat_age() or 999) < 120
    except Exception:
        pass
    prof_cache = state["profiles"].setdefault(mk, {"date": today})
    if prof_cache.get("date") != today:
        state["profiles"][mk] = prof_cache = {"date": today}
    dates_hist = intraday_bars.available_dates()[:-1][-20:]   # 오늘 제외 최근 20세션
    for sym in watch:
        df = intraday_bars.load_bars(sym)
        bars[sym] = df
        if df is not None and not df.empty:
            feats[sym] = compute_intraday_features(df)
        try:
            fresh[sym] = hb_ok and realtime_quotes.is_fresh(sym)
            obs[sym] = realtime_quotes.get_orderbook(sym) if mk == "KR" else \
                {"best_bid": realtime_quotes.best(sym, "sell"),
                 "best_ask": realtime_quotes.best(sym, "buy"),
                 "bids": [], "asks": []}
        except Exception:
            fresh[sym], obs[sym] = False, None
        if sym not in prof_cache and dates_hist:
            prof_cache[sym] = intraday_bars.build_minute_profile(sym, dates_hist)
        ob = obs.get(sym)
        if mk == "KR" and ob:
            from ml.intraday_axes import obi
            h = state["obi_hist"].setdefault(f"{mk}:{sym}", [])
            v = obi(ob)
            if v is not None:
                h.append([time.time(), round(v, 4)])
                del h[:-5]

    # 전 심볼 bar 정체 → 세션 이상(스트림 다운·조기폐장) — 전량 청산 후 대기
    latest_epochs = [int(df.index[-1].timestamp() // 60) for df in bars.values()
                     if df is not None and not df.empty]
    bars_stale = (not latest_epochs
                  or (time.time() // 60 - max(latest_epochs)) >= cfg["stale_flat_min"])
    if bars_stale and now_min > _OPEN_MIN[mk] + cfg["stale_flat_min"]:
        if any(k.startswith(f"{mk}:") for k in state["positions"]):
            _flatten_all(state, mk, "stale_flat", cfg, ledger, dry=dry, notes=notes)
        return notes

    # ③ 일손실 halt
    seed_env, seed_def = _SEED_ENV[mk]
    sleeve = _env_f(seed_env, seed_def) * cfg["sleeve_frac"] \
        + (state["counters"].get(mk) or {}).get("sleeve_pnl_cum", 0.0)
    c = state["counters"].setdefault(mk, {"trades": 0, "day_pnl": 0.0, "sleeve_pnl_cum": 0.0})
    if c.get("day_pnl", 0.0) <= -cfg["daily_loss_halt"] * max(sleeve, 1e-9):
        if not state["halt"].get(mk):
            notes.append(f"🛑 [{mk}] 일손실 한도 도달 ({c['day_pnl']:+,.0f}) — 당일 정지·전량 청산")
        state["halt"][mk] = True
    if state["halt"].get(mk):
        _flatten_all(state, mk, "halt_flat", cfg, ledger, dry=dry, notes=notes)

    from ml import intraday_axes as ax
    from ml import intraday_policy as ip
    params = ip.load_params(mk.lower())
    news_ev = _news_events(time.time())
    close_min = _CLOSE_MIN[mk]

    def _score(sym):
        axes = _symbol_axes(sym, mk, bars.get(sym), feats.get(sym),
                            profile=prof_cache.get(sym) or {},
                            obi_samples=[v for _, v in state["obi_hist"].get(f"{mk}:{sym}", [])],
                            news_events=news_ev, now_epoch=time.time(),
                            orb_minutes=cfg["orb_minutes"])
        if axes is None:
            return None, None
        return axes, ip.score({k: v for k, v in axes.items() if k != "_meta"}, params, mk.lower())

    # ④ 청산 — 진입보다 항상 먼저
    for key in [k for k in list(state["positions"]) if k.startswith(f"{mk}:")]:
        pos = state["positions"].get(key)
        if not pos:
            continue
        sym = pos["ticker"]
        df = bars.get(sym)
        last_key = state["last_processed_bar"].get(key)
        bar = None
        if df is not None and not df.empty:
            ep = int(df.index[-1].timestamp() // 60)
            if last_key != ep:
                bar = {"h": float(df["High"].iloc[-1]), "l": float(df["Low"].iloc[-1]),
                       "c": float(df["Close"].iloc[-1])}
        _, score = _score(sym) if bar else (None, None)
        cfg_exit = {"timestop_min": params.get("timestop_min", 90),
                    "theta_exit": params.get("theta_exit", 0.25),
                    "flat_buffer_min": cfg["flat_buffer_min"]}
        res = ax.check_exit(pos, bar, score, now_min, close_min, cfg_exit)
        if res:
            reason, ref_px = res
            if bar is None:                      # bar 부재 EOD — REST 가로 대체
                ref_px = _rest_price(sym, mk) or ref_px
            _do_exit(state, key, pos, reason, ref_px, mk, cfg, ledger,
                     orderbook=obs.get(sym), dry=dry, notes=notes)
        elif bar is not None:
            pos["last_score"] = score
            state["last_processed_bar"][key] = int(df.index[-1].timestamp() // 60)

    # ⑤ 진입
    if state["halt"].get(mk):
        return notes
    n_pos = sum(1 for k in state["positions"] if k.startswith(f"{mk}:"))
    cands = []
    for sym in universe:
        trade_sym = _trade_symbol(sym, mk, cfg)
        key = f"{mk}:{sym}"
        trade_key = f"{mk}:{trade_sym}"
        if trade_key in state["positions"]:
            continue
        df = bars.get(sym)
        if df is None or df.empty:
            continue
        ep = int(df.index[-1].timestamp() // 60)
        if state["last_processed_bar"].get(key) == ep:
            continue                              # 새 bar 없음 — 중복 판단 방지
        axes, score = _score(sym)
        state["last_processed_bar"][key] = ep
        if axes is None or score is None:
            continue
        trade_axes = _execution_axes(axes, trade_sym, bars.get(trade_sym), feats.get(trade_sym))
        if trade_axes is None:
            continue
        cands.append((score, sym, trade_sym, trade_axes))
    # 세션 최고점 진단 — 결정 0건이 휴면(점수 미달)인지 고장인지 로그만으로 구분.
    # 신고점 갱신 시에만 한 줄 기록(단조증가라 일 수 회) — 매분 스팸 없음.
    if cands:
        top_score, top_sym, top_trade, _ = max(cands)
        _today = state.get("session_date", {}).get(mk)
        _d = state.setdefault("diag", {}).get(mk) or {}
        if _d.get("date") != _today or top_score > float(_d.get("best") or -9):
            state["diag"][mk] = {"date": _today, "best": round(top_score, 4), "sym": top_sym,
                                 "trade_sym": top_trade}
            suffix = f"→{top_trade}" if top_trade != top_sym else ""
            logger.info("[%s] 세션 최고점 %s%s score %.2f (θ_entry %.2f / θ_explore %.2f)",
                        mk, top_sym, suffix, top_score, float(params.get("theta_entry", 0.55)),
                        float(_cfg_market_value(cfg, "explore_entry", mk, 0.48)))
    max_concurrent = _max_concurrent(mk, cfg)
    for score, signal_sym, sym, axes in sorted(cands, reverse=True):
        if max_concurrent > 0 and n_pos >= max_concurrent:
            break
        risk_mult, entry_mode = _entry_risk_mult(score, params, cfg, mk)
        if risk_mult <= 0:
            continue
        ob = obs.get(sym)
        meta = axes["_meta"]
        sp = ax.spread_bps((ob or {}).get("best_bid"), (ob or {}).get("best_ask"))
        if sp is None and mk == "US":
            # US 무버는 KIS 등록한도(41)상 호가(ask) 미구독이 정상 — 호가 부재를 hard-block
            # 하면 무버 진입이 영구 불가(라이브 실증: TSLA 0.61 차단). 스캐너 시총 하한($10B)
            # + 가상체결 보수 페널티(호가 없으면 2틱)가 방어하므로 통과. KR 은 10단계 호가
            # 구독이 보장되니 fail-closed 유지.
            sp = 0.0
        atr = meta.get("atr")
        _fr = ax.friction_per_share(meta["close"], mk,
                                    spread=((ob or {}).get("best_ask") or 0)
                                    - ((ob or {}).get("best_bid") or 0) or None)
        stop = (ax.stop_with_floor(meta["close"], atr, float(params.get("stop_atr_mult", 1.2)),
                                   _fr, floor_mult=cfg["stop_friction_mult"]) if atr else None)
        loss_budget = _remaining_loss_budget(state, mk, sleeve, cfg)
        qty = (ax.position_size(sleeve, cfg["risk_per_trade"] * risk_mult, meta["close"], stop,
                                friction=_fr, min_notional=cfg["min_notional"][mk],
                                loss_budget=loss_budget)
               if stop else 0)                      # _do_entry 와 동일 산식 — 가드 일관성
        ok, why = ax.entry_guards({
            "halt": state["halt"].get(mk, False), "now_min": now_min, "close_min": close_min,
            "open_min": _OPEN_MIN[mk], "open_buffer_min": _cfg_market_value(cfg, "open_buffer_min", mk, 0),
            "flat_buffer_min": cfg["flat_buffer_min"], "entry_cutoff_min": cfg["entry_cutoff_min"],
            "trades_today": c.get("trades", 0), "max_trades": cfg["max_trades"],
            "cooldown_ok": int(cfg.get("cooldown_min") or 0) <= 0
                           or time.time() >= float(state["cooldown_until"].get(f"{mk}:{sym}") or 0),
            "held": False, "fresh": fresh.get(sym, False),
            "spread": sp, "spread_cap": _entry_spread_cap(meta["close"], mk, cfg),
            "loss_budget": loss_budget, "qty": qty})
        if not ok:
            suffix = f"({signal_sym} 신호)" if signal_sym != sym else ""
            logger.info("[%s] %s%s score %.2f %s — 가드 차단(%s)", mk, sym, suffix, score, entry_mode, why)
            continue
        if _do_entry(state, sym, mk, axes, score, params, cfg, sleeve, ledger,
                     orderbook=ob, loss_budget=loss_budget, risk_mult=risk_mult,
                     entry_mode=entry_mode, dry=dry, notes=notes):
            n_pos += 1
    return notes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="쓰기 0 — 판단만 stdout")
    args = p.parse_args(argv)
    cfg = load_cfg()
    if not cfg["enabled"] and not args.dry_run:
        return 0                                  # 마스터 게이트 — 매분 크론 no-op
    state = load_state()
    if args.dry_run:
        state = json.loads(json.dumps(state))     # 사본 — 원본 state 불변
    all_notes: list[str] = []
    for mk in cfg["markets"]:
        if mk not in ("KR", "US"):
            continue
        try:
            all_notes += run_market(mk, state, cfg, dry=args.dry_run)
        except Exception as e:
            logger.exception("[%s] 엔진 예외: %s", mk, e)
    if args.dry_run:
        print("\n".join(all_notes) if all_notes else "(판단 없음)")
        return 0
    save_state(state)
    if all_notes:
        try:
            from lib.cron_common import send_cron_telegram
            send_cron_telegram("🕐 단기 모의\n" + "\n".join(all_notes[:15]))
        except Exception as e:
            logger.warning("알림 실패: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
