#!/usr/bin/env python3
"""scripts/gen_sp500_meta.py — 루트 sp500_meta.py 생성(재현).

sp500_seed.SP500 503티커 → yfinance `.info`(sector·marketCap) → 루트 `sp500_meta.py`
(SECTOR·MARKET_CAP·SECTOR_KR). S&P500 시장 맵(트리맵) 섹터 그룹·시총 타일 크기용.
당일 등락률은 라이브(dashboard.views.sp500_heatmap).

재실행(구성/시총 갱신 시): python scripts/gen_sp500_meta.py  (yfinance 네트워크·수분)
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sp500_seed import SP500

_SECTOR_KR = [
    ("Technology", "기술"), ("Financial Services", "금융"), ("Healthcare", "헬스케어"),
    ("Consumer Cyclical", "경기소비재"), ("Consumer Defensive", "필수소비재"),
    ("Communication Services", "커뮤니케이션"), ("Industrials", "산업재"),
    ("Energy", "에너지"), ("Utilities", "유틸리티"), ("Real Estate", "부동산"),
    ("Basic Materials", "소재"),
]


def _fetch(t: str):
    try:
        import yfinance as yf
        info = yf.Ticker(t).info or {}
        return t, (info.get("sector") or ""), float(info.get("marketCap") or 0.0)
    except Exception:
        return t, "", 0.0


def main() -> None:
    tickers = sorted(SP500)
    print(f"S&P500 {len(tickers)} — yfinance sector/marketCap fetch…", flush=True)
    sector: dict[str, str] = {}
    cap: dict[str, float] = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for i, (t, sec, mc) in enumerate(ex.map(_fetch, tickers), 1):
            if sec:
                sector[t] = sec
            if mc > 0:
                cap[t] = mc
            if i % 50 == 0:
                print(f"  {i}/{len(tickers)}", flush=True)
    today = datetime.date.today().isoformat()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "sp500_meta.py")
    with open(out, "w", encoding="utf-8") as f:
        f.write('"""sp500_meta.py — S&P500 섹터·시총 스냅샷 (시장 맵 트리맵용·표시).\n\n')
        f.write("scripts/gen_sp500_meta.py 로 생성(재현). SECTOR=yfinance 섹터, MARKET_CAP=시총 스냅샷\n")
        f.write("(트리맵 타일 크기·상대비율용). 당일 등락률은 라이브(views.sp500_heatmap).\n")
        f.write(f"생성일 {today} · 섹터 {len(sector)}·시총 {len(cap)}.\n")
        f.write('"""\n')
        f.write("from __future__ import annotations\n\n")
        f.write("# yfinance 섹터 → 한글 라벨 (트리맵 그룹 헤더). 미상 → '기타'\n")
        f.write("SECTOR_KR: dict[str, str] = {\n")
        for en, kr in _SECTOR_KR:
            f.write(f'    "{en}": "{kr}",\n')
        f.write("}\n\n")
        f.write("SECTOR: dict[str, str] = {\n")
        for t in sorted(sector):
            f.write(f'    "{t}": "{sector[t]}",\n')
        f.write("}\n\n")
        f.write("MARKET_CAP: dict[str, float] = {\n")
        for t in sorted(cap):
            f.write(f'    "{t}": {cap[t]:.0f},\n')
        f.write("}\n")
    print(f"wrote {out}: sector {len(sector)}, cap {len(cap)}", flush=True)


if __name__ == "__main__":
    main()
