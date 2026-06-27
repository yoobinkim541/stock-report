#!/usr/bin/env python3
"""kis_stream.py — KIS 실시간 시세 **읽기전용** WebSocket 상시 프로세스.

실시간 체결(가격·거래량)·호가를 KIS 실전 WS 에서 받아 ~/.cache/kis_realtime_quotes.json
(realtime_quotes.CACHE_PATH)에 coalesce 후 flush. 봇·크론 소비자는 realtime_quotes 로 읽음.

안전 (절대 불변):
  - **읽기전용 시세만** — 주문 경로/체결통보(AES) 없음(체결통보는 T7 별도 플래그). 시세 frame 만 처리.
  - WS 도메인(ops.koreainvestment.com:21000) 하드락(_assert_ws_url).
  - REALTIME_ENABLED=true 아니면 즉시 종료(watchdog hot-loop 방지). 실전키 없으면 fail-closed.
  - 美 스트림은 REALTIME_US_ENABLED=true (해외 실시간시세 신청 확인 후)일 때만 — 기본 KR 만.

env: REALTIME_ENABLED·REALTIME_US_ENABLED·REALTIME_KR_MAX(10)·REALTIME_US_MAX(10)·REALTIME_FLUSH_SECS(1.0)
크론(watchdog): * * * * * scripts/kis_stream_watchdog.sh
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

import requests

from providers import kis_quote, realtime_quotes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_WS_REAL = "ws://ops.koreainvestment.com:21000"   # ★ 하드락 — 실전 실시간 WS
_APPROVAL_PATH = "/oauth2/Approval"
_PID_FILE = os.path.expanduser("~/.local/state/stock-report/kis_stream.pid")

# 실시간 TR (시세 전용). 美 인덱스는 라이브 스모크 전 확정 금지(FLAG).
TR_KR_TRADE = "H0STCNT0"   # 국내 체결
TR_KR_ASK = "H0STASP0"     # 국내 호가(10단계)
TR_US_TRADE = "HDFSCNT0"   # 美 실시간지연체결가 (★미국=0분지연=무료 실시간; open-trading-api 확정)
TR_US_ASK = "HDFSASP0"     # 美 실시간호가 (★미국=무료 실시간 1호가; HDFSASP1 은 아시아 지연이라 미사용)

FLUSH_SECS = float(os.getenv("REALTIME_FLUSH_SECS", "1.0"))
KR_MAX = int(os.getenv("REALTIME_KR_MAX", "10"))
US_MAX = int(os.getenv("REALTIME_US_MAX", "10"))
WATCHLIST_REFRESH_SECS = int(os.getenv("REALTIME_WATCHLIST_REFRESH_SECS", "90"))

# 필드 인덱스 표 (KR=문서순·확정대상은 라이브 스모크). _ff 로 안전 캐스팅.
_KR_TRADE_PRICE_IDX, _KR_TRADE_VOL_IDX = 2, 13
_KR_ASK_BASE = {"ask1": 3, "bid1": 13, "askq1": 23, "bidq1": 33}  # 10단계 연속
# 美 (공식 컬럼 확정 — koreainvestment/open-trading-api):
#   HDFSCNT0 체결: SYMB[0]·LAST[10](현재가)·TVOL[19](누적거래량)
#   HDFSASP0 호가: PBID1[10]·PASK1[11]·VBID1[12]·VASK1[13] (미국 무료 실시간 1호가)
_US_TRADE_PRICE_IDX, _US_TRADE_VOL_IDX = 10, 19
_US_ASK_BID_IDX, _US_ASK_ASK_IDX = 10, 11
_US_ASK_BIDQ_IDX, _US_ASK_ASKQ_IDX = 12, 13


def is_enabled() -> bool:
    return os.getenv("REALTIME_ENABLED", "false").lower() == "true"


def us_enabled() -> bool:
    return os.getenv("REALTIME_US_ENABLED", "false").lower() == "true"


def _assert_ws_url(url: str) -> None:
    if not url.startswith(_WS_REAL):
        raise RuntimeError(f"[안전차단] 실시간 WS 도메인 외 접속 시도: {url}")


def _ff(rec: list, idx: int) -> float:
    try:
        return float(str(rec[idx]).replace(",", "").strip() or "0")
    except (IndexError, ValueError, TypeError):
        return 0.0


# ── 순수 함수 (폐형해 테스트 대상) ────────────────────────────────────────────

def build_subscribe(approval_key: str, tr_id: str, tr_key: str, register: bool = True) -> str:
    """실시간 등록(tr_type 1)/해제(2) 요청 JSON."""
    return json.dumps({
        "header": {"approval_key": approval_key, "custtype": "P",
                   "tr_type": "1" if register else "2", "content-type": "utf-8"},
        "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
    })


def handle_pingpong(raw: str) -> str | None:
    """PINGPONG 제어프레임이면 그대로 echo 반환, 아니면 None."""
    if not raw or not raw.lstrip().startswith("{"):
        return None
    try:
        j = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if (j.get("header") or {}).get("tr_id") == "PINGPONG":
        return raw
    return None


def _extract_kr_trade(f: list) -> dict:
    return {"symbol": f[0], "kind": "trade",
            "price": _ff(f, _KR_TRADE_PRICE_IDX) or None, "volume": _ff(f, _KR_TRADE_VOL_IDX)}


def _extract_kr_ask(f: list, depth: int = 10) -> dict:
    asks, bids = [], []
    b = _KR_ASK_BASE
    for i in range(depth):
        ap, aq = _ff(f, b["ask1"] + i), _ff(f, b["askq1"] + i)
        bp, bq = _ff(f, b["bid1"] + i), _ff(f, b["bidq1"] + i)
        if ap > 0:
            asks.append((ap, aq))
        if bp > 0:
            bids.append((bp, bq))
    return {"symbol": f[0], "kind": "ask", "asks": asks, "bids": bids,
            "best_ask": asks[0][0] if asks else None, "best_bid": bids[0][0] if bids else None}


def _norm_us_symbol(raw: str) -> str:
    """수신 SYMB 정규화 — 'DNASAAPL'/'RBAQAAPL' 류 시장접두 제거(있으면). 기본은 그대로."""
    s = (raw or "").upper()
    for pre in ("DNAS", "DNYS", "DAMS", "RBAQ", "RBAY", "RBAA"):
        if s.startswith(pre) and len(s) > len(pre):
            return s[len(pre):]
    return s


def _us_ws_key(symbol: str) -> str:
    """美 실시간 tr_key — 정규장: 'D'+거래소(NAS/NYS/AMS)+종목 (예 DNASAAPL). 주간거래(R+BAQ/BAY/BAA)는 미지원."""
    return "D" + kis_quote._us_excd(symbol) + symbol.upper()


def _extract_us_trade(f: list) -> dict:
    return {"symbol": _norm_us_symbol(f[0]), "kind": "trade",
            "price": _ff(f, _US_TRADE_PRICE_IDX) or None, "volume": _ff(f, _US_TRADE_VOL_IDX)}


def _extract_us_ask(f: list) -> dict:
    bid, ask = _ff(f, _US_ASK_BID_IDX), _ff(f, _US_ASK_ASK_IDX)
    bq, aq = _ff(f, _US_ASK_BIDQ_IDX), _ff(f, _US_ASK_ASKQ_IDX)
    return {"symbol": _norm_us_symbol(f[0]), "kind": "ask",
            "asks": [(ask, aq)] if ask > 0 else [], "bids": [(bid, bq)] if bid > 0 else [],
            "best_ask": ask or None, "best_bid": bid or None}


_EXTRACTORS = {
    TR_KR_TRADE: _extract_kr_trade, TR_KR_ASK: _extract_kr_ask,
    TR_US_TRADE: _extract_us_trade, TR_US_ASK: _extract_us_ask,
}
_WIDTHS = {TR_KR_TRADE: 46, TR_KR_ASK: 59, TR_US_TRADE: 25, TR_US_ASK: 16}   # multi-record 분할용(단일프레임은 무관)


def parse_realtime_frame(raw: str) -> list[dict]:
    """실시간 데이터프레임(`<enc>|<tr_id>|<count>|<^구분 payload>`) → 정규화 레코드 list.

    제어/JSON(PINGPONG·구독ACK)·암호화(enc=1)·미지원 tr_id 는 [] 반환.
    """
    if not raw or raw[0] not in ("0", "1"):
        return []
    if raw[0] == "1":
        return []   # 암호화(체결통보) — 시세 스트림서 미처리(T7 별도)
    parts = raw.split("|", 3)
    if len(parts) < 4:
        return []
    _enc, tr_id, cnt_s, payload = parts
    ext = _EXTRACTORS.get(tr_id)
    if not ext:
        return []
    try:
        cnt = max(1, int(cnt_s))
    except (ValueError, TypeError):
        cnt = 1
    fields = payload.split("^")
    width = _WIDTHS.get(tr_id) or len(fields)
    out = []
    for i in range(cnt):
        rec = fields if cnt == 1 else fields[i * width:(i + 1) * width]
        if len(rec) < 2:
            continue
        try:
            out.append(ext(rec))
        except Exception:
            continue
    return out


def select_watchlist(kr_syms: list[str], us_syms: list[str], *,
                     kr_max: int = KR_MAX, us_max: int = US_MAX) -> tuple[dict, dict]:
    """우선순위 리스트(보유∪활성알림) → 시장별 dedup+캡. (선택, 드롭) 반환. 캡=종목수(체결+호가=2등록/종목)."""
    def dedup(xs):
        seen = []
        for x in xs:
            x = (x or "").strip().upper()
            if x and x not in seen:
                seen.append(x)
        return seen
    kr, us = dedup(kr_syms), dedup(us_syms)
    selected = {"KR": kr[:kr_max], "US": us[:us_max]}
    dropped = {"KR": kr[kr_max:], "US": us[us_max:]}
    return selected, dropped


def compute_watchlist() -> tuple[dict, dict]:
    """보유(국내+해외) ∪ 활성 가격알림 → select_watchlist. 소스는 시스템 단일진실원 재사용."""
    kr, us = [], []
    try:
        import portfolio_universe
        for t in portfolio_universe.load_portfolio_tickers():
            (kr if t.isdigit() else us).append(t)
    except Exception as e:
        logger.warning("보유 티커 로드 실패: %s", e)
    try:
        from bot import price_alerts
        for a in price_alerts.load_alerts():
            if not a.get("triggered"):
                t = (a.get("ticker") or "").strip().upper()
                (kr if t.isdigit() else us).append(t)
    except Exception as e:
        logger.warning("알림 티커 로드 실패: %s", e)
    return select_watchlist(kr, us)


# ── 인증 / 캐시 (네트워크·IO) ─────────────────────────────────────────────────

def _get_approval_key() -> str | None:
    if not kis_quote._key() or not kis_quote._secret():
        logger.error("실전 앱키 없음 — WS approval 불가(fail-closed)")
        return None
    url = kis_quote._QUOTE_BASE + _APPROVAL_PATH
    kis_quote._assert_quote_url(url)
    try:
        r = requests.post(url, json={"grant_type": "client_credentials",
                                     "appkey": kis_quote._key(), "secretkey": kis_quote._secret()},
                          timeout=15, allow_redirects=False)
        r.raise_for_status()
        return r.json().get("approval_key")
    except Exception as e:
        logger.error("approval_key 발급 실패: %s", e)
        return None


def _flush(latest: dict, market: str, connected: bool = True) -> None:
    try:
        import safe_io
        out = {k: v for k, v in latest.items()}
        out[realtime_quotes.HEARTBEAT_KEY] = {"ts": time.time(), "connected": connected, "market": market}
        safe_io.atomic_write_json(realtime_quotes.CACHE_PATH, out)
    except Exception as e:
        logger.debug("실시간 캐시 flush 실패(무시): %s", e)


def _apply(latest: dict, rec: dict, *, delayed: bool) -> None:
    sym = rec.get("symbol")
    if not sym:
        return
    e = latest.setdefault(sym, {"src": "kis_ws"})
    e["ts"] = time.time()
    e["delayed"] = delayed
    if rec["kind"] == "trade":
        if rec.get("price"):
            e["price"] = rec["price"]
        if rec.get("volume") is not None:
            e["volume"] = rec["volume"]
    else:
        e.update(best_ask=rec.get("best_ask"), best_bid=rec.get("best_bid"),
                 asks=rec.get("asks"), bids=rec.get("bids"))
        if rec.get("best_ask") and not e.get("price"):
            e["price"] = rec["best_ask"]


# ── 비동기 루프 (라이브·MH 검증) ─────────────────────────────────────────────

async def _session(approval_key: str) -> None:
    import websockets
    _assert_ws_url(_WS_REAL)
    sel, dropped = compute_watchlist()
    if dropped["KR"] or dropped["US"]:
        logger.warning("워치리스트 캡 초과 드롭: %s", dropped)
    latest: dict = {}
    async with websockets.connect(_WS_REAL, ping_interval=None, max_size=None) as ws:
        # 등록: KR 체결+호가, (US 활성 시) 해외 체결+호가
        subs = [(TR_KR_TRADE, s) for s in sel["KR"]] + [(TR_KR_ASK, s) for s in sel["KR"]]
        if us_enabled():
            us_keys = [_us_ws_key(s) for s in sel["US"]]   # 美 tr_key = D+거래소+종목 (DNASAAPL)
            subs += [(TR_US_TRADE, k) for k in us_keys] + [(TR_US_ASK, k) for k in us_keys]
        for tr_id, key in subs:
            await ws.send(build_subscribe(approval_key, tr_id, key, register=True))
        logger.info("구독 %d건 (KR %d·US %d, US스트림=%s)", len(subs),
                    len(sel["KR"]), len(sel["US"]), us_enabled())

        last_flush = last_refresh = time.time()
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=FLUSH_SECS)
            except asyncio.TimeoutError:
                raw = None
            if raw is not None:
                pong = handle_pingpong(raw)
                if pong is not None:
                    await ws.send(pong)
                else:
                    for rec in parse_realtime_frame(raw):
                        _apply(latest, rec, delayed=False)   # KR·美 모두 무료 실시간(美 0분지연)
            now = time.time()
            if now - last_flush >= FLUSH_SECS:
                _flush(latest, market="KR/US" if us_enabled() else "KR", connected=True)
                last_flush = now
            if now - last_refresh >= WATCHLIST_REFRESH_SECS:
                new_sel, _ = compute_watchlist()
                if new_sel != sel:    # diff 등록/해제 (재구독으로 단순화 — 캡 내라 안전)
                    raise _Resubscribe()
                last_refresh = now


class _Resubscribe(Exception):
    pass


async def main_async() -> None:
    approval = _get_approval_key()
    if not approval:
        return
    backoff = 1.0
    while True:
        try:
            await _session(approval)
        except _Resubscribe:
            logger.info("워치리스트 변경 — 재구독")
            backoff = 1.0
            continue
        except Exception as e:
            logger.warning("WS 세션 종료(%s) — %.0fs 후 재접속", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)
            approval = _get_approval_key() or approval   # 재발급 시도
            continue


def main() -> int:
    if not is_enabled():
        logger.info("REALTIME_ENABLED 아님 — 실시간 스트림 미기동")
        return 0
    if not kis_quote._key():
        logger.error("실전 앱키 없음 — 미기동(fail-closed)")
        return 0
    os.makedirs(os.path.dirname(_PID_FILE), exist_ok=True)
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    logger.info("=== kis_stream 시작 (PID %d) ===", os.getpid())
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
