#!/usr/bin/env python3
"""paper_track.py — MetaAllocator vs Phase 규칙 A/B 페이퍼 트레이딩 추적

매일 두 배분(MetaAllocator 권장 비중 / Phase 규칙 비중)을 기록하고,
5·20거래일 경과 후 실현 수익률을 채워 넣는다. 데이터가 30건 이상 쌓이면
월요일마다 Sharpe 비교 요약을 텔레그램으로 발송한다.

크론 (평일 22:50 UTC = 07:50 KST):
    50 22 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python crons/paper_track.py >> /tmp/paper_track.log 2>&1

출력: ~/.local/share/stock-report/paper_track.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST        = timezone(timedelta(hours=9))
TRACK_PATH = Path.home() / ".local" / "share" / "stock-report" / "paper_track.json"
STATE_PATH = Path.home() / ".cache" / "barbell_state.json"
MIN_SUMMARY_ENTRIES = 30


def _load_track() -> dict:
    if TRACK_PATH.exists():
        try:
            return json.loads(TRACK_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_track(track: dict) -> None:
    TRACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRACK_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(track, indent=1, ensure_ascii=False))
    tmp.replace(TRACK_PATH)


def _current_phase() -> tuple[str, object]:
    state = json.loads(STATE_PATH.read_text())
    mt = state.get("market_type", "neutral")
    pk = state.get("phase_key", 0)
    if isinstance(pk, str) and pk.isdigit():
        pk = int(pk)
    return mt, pk


def record_today(track: dict) -> None:
    """오늘의 meta / rule 비중 기록 (이미 있으면 스킵)."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if today in track:
        logger.info("%s 이미 기록됨 — 스킵", today)
        return

    mt, pk = _current_phase()

    from ml.meta_allocator import get_meta_allocation
    alloc  = get_meta_allocation(mt, pk)
    meta_w = {t: round(w, 4) for t, w in alloc.weights.items() if w > 0.001}

    # Phase 규칙 비중: ML 블렌딩 없는 원시 DCA 비중 (calculate_dca와 동일한 선택 로직)
    from barbell_strategy import load_dca_weights
    w_normal, w_bear = load_dca_weights()
    rule_w = w_bear if (mt == "bear" and isinstance(pk, int) and pk >= 2) else w_normal
    rule_w = {t: round(w, 4) for t, w in rule_w.items() if w > 0.001}

    track[today] = {
        "market_type": mt,
        "phase_key":   str(pk),
        "meta":        meta_w,
        "rule":        rule_w,
        "regime":      alloc.regime,
        "confidence":  round(alloc.confidence, 3),
    }
    logger.info("%s 기록 — regime=%s conf=%.2f meta %d종목 / rule %d종목",
                today, alloc.regime, alloc.confidence, len(meta_w), len(rule_w))


def _weighted_forward_return(weights: dict, closes: dict, start: str, horizon: int) -> float | None:
    """기록일 종가 → horizon 거래일 후 종가의 가중 수익률. 데이터 부족 시 None."""
    import pandas as pd
    total_w, acc = 0.0, 0.0
    for t, w in weights.items():
        s = closes.get(t)
        if s is None or s.empty:
            continue
        s = s.dropna()
        pos = s.index.searchsorted(pd.Timestamp(start))
        if pos >= len(s) or pos + horizon >= len(s):
            return None   # 아직 미래 데이터 없음
        r = float(s.iloc[pos + horizon] / s.iloc[pos] - 1)
        acc     += w * r
        total_w += w
    if total_w < 0.5:
        return None
    return acc / total_w


def fill_realized(track: dict) -> int:
    """과거 기록의 5d/20d 실현 수익률 채우기. 갱신 건수 반환."""
    pending = [
        (d, e) for d, e in track.items()
        if ("ret_meta_20d" not in e or "ret_meta_5d" not in e)
    ]
    if not pending:
        return 0

    tickers = set()
    for _, e in pending:
        tickers |= set(e["meta"]) | set(e["rule"])

    from ml.data_pipeline import fetch_prices
    prices = fetch_prices(sorted(tickers), days=120)
    closes = {t: df["Close"] for t, df in prices.items() if df is not None and not df.empty}

    updated = 0
    for d, e in pending:
        changed = False
        for horizon, key in ((5, "5d"), (20, "20d")):
            for side in ("meta", "rule"):
                field = f"ret_{side}_{key}"
                if field in e:
                    continue
                r = _weighted_forward_return(e[side], closes, d, horizon)
                if r is not None:
                    e[field] = round(r, 5)
                    changed = True
        if changed:
            updated += 1
    return updated


def summarize(track: dict) -> str | None:
    """meta vs rule 비교 요약 (5d 수익 기준 Sharpe). 데이터 부족 시 None."""
    rows = [(e["ret_meta_5d"], e["ret_rule_5d"]) for e in track.values()
            if "ret_meta_5d" in e and "ret_rule_5d" in e]
    if len(rows) < MIN_SUMMARY_ENTRIES:
        return None
    meta = np.array([r[0] for r in rows])
    rule = np.array([r[1] for r in rows])

    def _sharpe(x: np.ndarray) -> float:
        return float(x.mean() / x.std() * np.sqrt(252 / 5)) if x.std() > 0 else 0.0

    # 변동성 조정 비교: meta(현금 비중 높음)와 rule(주식 100%)은 리스크 수준이 달라
    # 단순 평균 비교는 불공정 — meta 수익을 rule 변동성 수준으로 스케일해 동일 리스크 비교
    vol_scale = rule.std() / meta.std() if meta.std() > 0 else 1.0
    meta_voladj = meta.mean() * vol_scale

    return "\n".join([
        "🧪 페이퍼 트레이딩 A/B (MetaAllocator vs Phase 규칙)",
        "━━━━━━━━━━━━━━",
        f"표본: {len(rows)}일 (5거래일 수익 기준)",
        f"Meta:  평균 {meta.mean()*100:+.2f}%  Sharpe {_sharpe(meta):.2f}",
        f"Rule:  평균 {rule.mean()*100:+.2f}%  Sharpe {_sharpe(rule):.2f}",
        f"동일 리스크 환산 Meta 평균: {meta_voladj*100:+.2f}% (vol ×{vol_scale:.2f})",
        f"우세:  {'Meta' if _sharpe(meta) > _sharpe(rule) else 'Rule'} (Sharpe 기준)",
        "→ Meta 우세 지속 시 _ml_dca_blend 반영 비율 상향 검토",
    ])


def _send(text: str) -> None:
    token, chat = os.getenv("STOCK_BOT_TOKEN"), os.getenv("STOCK_BOT_CHAT_ID")
    if not token or not chat:
        return
    import requests
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=15)
    except Exception as e:
        logger.warning("텔레그램 발송 실패: %s", e)


def main() -> int:
    logger.info("=== paper_track 시작 ===")
    track = _load_track()
    try:
        record_today(track)
    except Exception as e:
        logger.error("오늘 기록 실패: %s", e)
    try:
        n = fill_realized(track)
        logger.info("실현 수익 갱신: %d건", n)
    except Exception as e:
        logger.error("실현 수익 갱신 실패: %s", e)
    _save_track(track)

    # 월요일에만 요약 발송 (데이터 충분 시)
    if datetime.now(KST).weekday() == 0:
        s = summarize(track)
        if s:
            _send(s)
            logger.info("A/B 요약 발송")
    return 0


if __name__ == "__main__":
    sys.exit(main())
