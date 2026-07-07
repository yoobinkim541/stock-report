#!/usr/bin/env python3
"""intraday_bars.py — 단기(1분봉) 데이터층: 틱→분봉 집계 + JSONL 저장/로드 + 심볼 변환.

kis_stream(유일 writer)이 BarAggregator 로 틱을 1분 OHLCV 로 확정해
~/reports/ml-data/intraday_bars/{YYYY-MM-DD(UTC)}.jsonl 에 append 하고,
단기 모의 엔진(crons/intraday_mock_track)·대시보드가 load_bars 로 읽는다.

bar 레코드 (1줄 1bar, append-only — Ledger 관례):
  {ts(시장 로컬 ISO·bar 시작 분), epoch_min, symbol(base), market, o,h,l,c,
   v(당일 누적거래량 차분), n(틱수), v_partial(세션 첫 관측), v_anom(누적 역행), src}

주의:
  - KIS WS 틱의 volume 은 **당일 누적 거래량** → bar 볼륨은 차분. 누적 역행(글리치)은 0 클램프+v_anom.
  - 틱에 체결시각 필드가 없어 WS 수신시각 기준으로 분 경계를 나눈다(허용 오차 ~1초).
  - 미완성 분은 프로세스 재시작 시 소실 허용(다음 분부터 재개).
  - 모듈 임포트는 경량 유지(pandas 는 reader 내부 import) — kis_stream 이 상시 임포트.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BAR_DIR = Path(os.path.expanduser("~/reports/ml-data/intraday_bars"))
_TZ = {"KR": ZoneInfo("Asia/Seoul"), "US": ZoneInfo("America/New_York")}


# ── 심볼 변환 단일 진실원 (bar store·state·ledger·실시간 캐시 = base 표기) ────

def base_symbol(ticker: str) -> str:
    """"005930.KS"→"005930" · "AAPL"→"AAPL" — 저장/캐시 키 표기."""
    t = (ticker or "").strip().upper()
    for suf in (".KS", ".KQ"):
        if t.endswith(suf):
            return t[: -len(suf)]
    return t


def market_of(ticker: str) -> str:
    """6자리 숫자 코드 → "KR", 그 외 "US" (kis_stream._classify 와 동일 규칙)."""
    base = (ticker or "").strip().upper().split(".")[0]
    return "KR" if base.isdigit() and len(base) == 6 else "US"


def to_yf(symbol: str, market: str | None = None) -> str:
    """yfinance 티커 표기 — KR 은 .KS 기본(.KQ 는 원 표기에 명시된 경우 보존), US 그대로."""
    t = (symbol or "").strip().upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        return t
    mk = market or market_of(t)
    return f"{t}.KS" if mk == "KR" else t


# ── 집계 (순수·클록 주입 — kis_stream 전용 writer) ────────────────────────────

class BarAggregator:
    """틱 스트림 → 1분 OHLCV 확정. 볼륨은 당일 누적 차분.

    on_tick 이 분 경계를 넘기면 이전 bar 를 확정 큐로 이동, roll(now) 이 시간 경과로도
    확정(틱 없는 분 대비)해 확정분을 회수한다. 상태는 메모리 한정(재시작 소실 허용).
    """

    def __init__(self):
        self._cur: dict[str, dict] = {}     # sym → 진행 중 bar
        self._prev_cum: dict[str, float] = {}   # sym → 직전 관측 누적거래량
        self._done: list[dict] = []
        self._allowed: set[str] | None = None   # 구독 심볼 화이트리스트 (None=전부 허용)

    def set_allowed(self, symbols) -> None:
        """구독 중인 심볼만 집계 — 멀티레코드 프레임 폭 어긋남으로 rec[0]에 가격/거래량이
        오는 파싱 글리치가 bar store(학습 데이터)를 오염시키는 것을 원천 차단.
        (6자리 숫자 거래량은 KR 심볼 패턴으로 위장하므로 패턴 검증으론 부족 — 라이브 실증.)"""
        self._allowed = set(symbols) if symbols is not None else None

    def on_tick(self, symbol: str, price: float, cum_volume, ts_epoch: float,
                market: str = "KR") -> None:
        if not symbol or not price or price <= 0:
            return
        if self._allowed is not None and symbol not in self._allowed:
            return
        minute = int(ts_epoch // 60)
        cur = self._cur.get(symbol)
        if cur is not None and cur["minute"] != minute:
            self._finalize(symbol)
            cur = None
        if cur is None:
            prev = self._prev_cum.get(symbol)
            cur = {"minute": minute, "market": market, "o": price, "h": price,
                   "l": price, "c": price, "n": 0,
                   # 세션 첫 관측 bar 는 이전 누적을 모름 → 첫 틱 누적을 기준(과소집계)·v_partial
                   "cum_open": prev if prev is not None else (float(cum_volume) if cum_volume else 0.0),
                   "cum_last": float(cum_volume) if cum_volume else 0.0,
                   "v_partial": prev is None}
            self._cur[symbol] = cur
        cur["h"] = max(cur["h"], price)
        cur["l"] = min(cur["l"], price)
        cur["c"] = price
        cur["n"] += 1
        if cum_volume is not None:
            try:
                cur["cum_last"] = max(cur["cum_last"], float(cum_volume))
                self._prev_cum[symbol] = cur["cum_last"]
            except (TypeError, ValueError):
                pass

    def _finalize(self, symbol: str) -> None:
        cur = self._cur.pop(symbol, None)
        if not cur:
            return
        delta = cur["cum_last"] - cur["cum_open"]
        v_anom = delta < 0
        tz = _TZ.get(cur["market"], timezone.utc)
        self._done.append({
            "ts": datetime.fromtimestamp(cur["minute"] * 60, tz=tz).isoformat(),
            "epoch_min": cur["minute"], "symbol": symbol, "market": cur["market"],
            "o": cur["o"], "h": cur["h"], "l": cur["l"], "c": cur["c"],
            "v": max(0.0, delta), "n": cur["n"],
            "v_partial": bool(cur["v_partial"]), "v_anom": bool(v_anom), "src": "kis_ws",
        })

    def roll(self, now_epoch: float) -> list[dict]:
        """분 경계를 지난 진행분을 확정하고 확정 큐 전체를 회수."""
        now_min = int(now_epoch // 60)
        for sym in [s for s, c in self._cur.items() if c["minute"] < now_min]:
            self._finalize(sym)
        out, self._done = self._done, []
        return out


def bar_path(date_utc: str, base_dir: Path | str | None = None) -> Path:
    return Path(base_dir or BAR_DIR) / f"{date_utc}.jsonl"


def append_bars(bars: list[dict], base_dir: Path | str | None = None) -> int:
    """확정 bar append (단일 writer=kis_stream 전제 — 락 불필요). 반환: 기록 건수."""
    if not bars:
        return 0
    d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = bar_path(d, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for b in bars:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
    return len(bars)


# ── reader (엔진·대시보드·학습) ───────────────────────────────────────────────

def _read_rows(date_utc: str, symbol: str | None = None,
               base_dir: Path | str | None = None) -> list[dict]:
    path = bar_path(date_utc, base_dir)
    if not path.exists():
        return []
    sym = base_symbol(symbol) if symbol else None
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if sym is None or r.get("symbol") == sym:
                    rows.append(r)
    except OSError as e:
        logger.debug("bar 파일 읽기 실패(%s): %s", path, e)
    return rows


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def available_dates(base_dir: Path | str | None = None) -> list[str]:
    """bar 파일이 존재하는 날짜(YYYY-MM-DD) 오름차순."""
    d = Path(base_dir or BAR_DIR)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("????-??-??.jsonl"))


def list_symbols(date_utc: str, market: str | None = None,
                 base_dir: Path | str | None = None) -> list[str]:
    """그날 bar 가 있는 심볼 목록 (대시보드 선택용)."""
    seen: list[str] = []
    for r in _read_rows(date_utc, None, base_dir):
        if market and r.get("market") != market:
            continue
        s = r.get("symbol")
        if s and s not in seen:
            seen.append(s)
    return seen


def load_bars(symbol: str, date_utc: str | None = None, *, interval: str = "1m",
              base_dir: Path | str | None = None):
    """자체 bar store → OHLCV DataFrame (tz-aware 인덱스·compute_intraday_features 호환).

    빈 결과는 빈 DataFrame (graceful). interval="5m" 은 1m 리샘플.
    """
    import pandas as pd
    rows = _read_rows(date_utc or today_utc(), symbol, base_dir)
    if not rows:
        return pd.DataFrame()
    dedup: dict[int, dict] = {}
    for r in rows:
        try:
            dedup[int(r["epoch_min"])] = r
        except (KeyError, TypeError, ValueError):
            continue
    rows = [dedup[k] for k in sorted(dedup)]
    idx = pd.DatetimeIndex(pd.to_datetime([r["ts"] for r in rows]))
    df = pd.DataFrame({
        "Open": [r.get("o") for r in rows], "High": [r.get("h") for r in rows],
        "Low": [r.get("l") for r in rows], "Close": [r.get("c") for r in rows],
        "Volume": [r.get("v", 0.0) for r in rows],
    }, index=idx)
    if interval == "5m":
        df = (df.resample("5min")
                .agg({"Open": "first", "High": "max", "Low": "min",
                      "Close": "last", "Volume": "sum"})
                .dropna(subset=["Open"]))
    return df


def load_bars_with_fallback(symbol: str, market: str | None = None,
                            date_utc: str | None = None, *, interval: str = "1m"):
    """bar store 우선, 없으면 yfinance 폴백 (대시보드·백필 전용 — 엔진 핫패스 금지).

    반환 (DataFrame, src) — src ∈ {"store", "yfinance", "none"}.
    """
    df = load_bars(symbol, date_utc, interval=interval)
    if df is not None and not getattr(df, "empty", True):
        return df, "store"
    mk = market or market_of(symbol)
    try:
        from ml.intraday_signal import fetch_intraday
        cands = [to_yf(symbol, mk)]
        if mk == "KR" and cands[0].endswith(".KS"):
            cands.append(cands[0][:-3] + ".KQ")   # 스캐너 코드는 시장 미상 — 코스닥 재시도
        for yf_t in cands:
            df = fetch_intraday(yf_t, interval=interval, days=1)
            if df is not None and not getattr(df, "empty", True):
                return df, "yfinance"
    except Exception as e:
        logger.debug("yfinance 폴백 실패(%s): %s", symbol, e)
    import pandas as pd
    return pd.DataFrame(), "none"


# ── 분대별 거래량 프로파일 (volspike 시간대 정규화 원천) ──────────────────────

def build_minute_profile(symbol: str, dates: list[str] | None = None, *,
                         base_dir: Path | str | None = None,
                         max_sessions: int = 20) -> dict:
    """최근 ≤max_sessions 세션의 같은 분대(HH:MM 시장 로컬) 거래량 mean/std.

    반환 {"HH:MM": {"mean": v, "std": v, "n": k}} — v_partial 제외. 표본 없으면 {}.
    """
    ds = dates if dates is not None else available_dates(base_dir)[-(max_sessions + 1):]
    sym = base_symbol(symbol)
    buckets: dict[str, list[float]] = {}
    for d in ds[-max_sessions:]:
        for r in _read_rows(d, sym, base_dir):
            if r.get("v_partial") or r.get("v_anom"):
                continue
            hhmm = str(r.get("ts", ""))[11:16]
            if len(hhmm) == 5:
                buckets.setdefault(hhmm, []).append(float(r.get("v", 0.0)))
    out = {}
    for hhmm, vs in buckets.items():
        n = len(vs)
        mean = sum(vs) / n
        var = sum((v - mean) ** 2 for v in vs) / n
        out[hhmm] = {"mean": mean, "std": math.sqrt(var), "n": n}
    return out
