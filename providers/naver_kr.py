#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""providers/naver_kr.py — KR 투자자 수급 + KOSPI200 멤버십 (Naver) — pykrx 공백 복구 (Phase B+).

이 서버에서 data.krx.co.kr(pykrx) 는 403 이나 **Naver 는 동작**(검증). pykrx 가 못 주던:
  - 투자자 수급(외국인/기관/개인 순매수): `m.stock.naver.com/api/stock/{code}/trend` (JSON, ~60일 rolling)
  - KOSPI200 현재 멤버십: `finance.naver.com/sise/entryJongmok.naver?type=KPI200` (EUC-KR HTML)

한계: 수급은 ~60일만(과거 학습엔 forward 스냅샷 축적 필요). KOSPI200 과거 시점별은 무료 부재 →
현재 멤버십만(forward 스냅샷으로 이력 축적). Naver HTML 은 **EUC-KR**(UTF-8 로 읽으면 빈 결과 함정).

_get 은 네트워크(테스트는 monkeypatch). 파싱은 순수.
"""
from __future__ import annotations

import json
import logging
import re
logger = logging.getLogger(__name__)


def _get(url: str) -> bytes:
    from lib.http_utils import http_get
    return http_get(url, timeout=20)


def _num(s):
    """'-5,975,701' / '+9,298,204' → int. 실패 None."""
    try:
        return int(str(s).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def _pct(s):
    """'47.27%' → 0.4727. 실패 None."""
    try:
        return round(float(str(s).replace("%", "").strip()) / 100.0, 4)
    except (TypeError, ValueError):
        return None


def _parse_trend(j) -> list[dict]:
    """trend JSON → [{date, foreign_net, inst_net, indiv_net, foreign_ratio, close}] (최신순). 순수."""
    rows = j if isinstance(j, list) else (j.get("trends") or j.get("result") or [])
    out = []
    for r in rows:
        out.append({
            "date": str(r.get("bizdate")),
            "foreign_net": _num(r.get("foreignerPureBuyQuant")),
            "inst_net": _num(r.get("organPureBuyQuant")),
            "indiv_net": _num(r.get("individualPureBuyQuant")),
            "foreign_ratio": _pct(r.get("foreignerHoldRatio")),
            "close": _num(r.get("closePrice")),
        })
    return out


def investor_flow(code: str, *, days: int = 20) -> list[dict]:
    """종목 최근 수급(외인/기관/개인 순매수, 최신순). 실패 시 []."""
    from providers.kr_market_data import norm_code
    code = norm_code(code)
    try:
        raw = _get(f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize={max(1, min(days, 60))}")
        return _parse_trend(json.loads(raw))[:days]
    except Exception as e:
        logger.warning("naver 수급 실패 %s: %s", code, e)
        return []


def investor_flow_features(code: str) -> dict:
    """라이브 수급 피처 — 외인·기관 순매수 5/20일 합·스마트머니(외인+기관)·외인보유율·연속순매수일.

    수급량은 주식수 단위(종목 간 비교 위해 부호/추세 중심). 결측 None.
    """
    flow = investor_flow(code, days=20)
    out = {"foreign_net_5d": None, "inst_net_5d": None, "smart_net_20d": None,
           "foreign_ratio": None, "foreign_buy_streak": None, "n": len(flow)}
    if not flow:
        return out
    f5 = [r["foreign_net"] for r in flow[:5] if r["foreign_net"] is not None]
    i5 = [r["inst_net"] for r in flow[:5] if r["inst_net"] is not None]
    sm = [(r["foreign_net"] or 0) + (r["inst_net"] or 0) for r in flow
          if r["foreign_net"] is not None or r["inst_net"] is not None]
    out["foreign_net_5d"] = sum(f5) if f5 else None
    out["inst_net_5d"] = sum(i5) if i5 else None
    out["smart_net_20d"] = sum(sm) if sm else None
    out["foreign_ratio"] = flow[0].get("foreign_ratio")
    streak = 0
    for r in flow:                         # 최신부터 외인 순매수(+) 연속일
        if r["foreign_net"] is not None and r["foreign_net"] > 0:
            streak += 1
        else:
            break
    out["foreign_buy_streak"] = streak
    return out


def kospi200_members() -> list[str]:
    """현재 KOSPI200 구성 종목코드(6자리). EUC-KR 페이지 1~20 순회. 실패 시 []."""
    codes: set = set()
    try:
        for page in range(1, 21):
            html = _get(f"https://finance.naver.com/sise/entryJongmok.naver?type=KPI200&page={page}").decode("euc-kr", errors="replace")
            found = re.findall(r"code=(\d{6})", html)
            if not found:
                break
            codes |= set(found)
    except Exception as e:
        logger.warning("naver KOSPI200 실패: %s", e)
    return sorted(codes)
