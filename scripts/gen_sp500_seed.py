#!/usr/bin/env python3
"""scripts/gen_sp500_seed.py — 루트 sp500_seed.py 생성(재현).

현재 S&P500 구성(providers.index_membership, 레포내 fja05680 이력·생존편향0)
+ yfinance shortName → 루트 `sp500_seed.py` (SP500: dict[티커→영문명]).
표시·검색용 시드 — ticker_names.py 가 병합해 대시보드 검색 유니버스로 노출.

재실행(구성/이름 갱신 시): python scripts/gen_sp500_seed.py  (yfinance 네트워크·수분)
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import index_membership as im


def _norm(t: str) -> str:
    return t.strip().upper().replace(".", "-")


def _clean(name, ticker: str) -> str:
    if not name:
        return ticker
    n = str(name).replace(" (The)", "").strip()
    return n or ticker


def _fetch(t: str):
    try:
        import yfinance as yf
        info = yf.Ticker(t).info or {}
        nm = info.get("shortName") or info.get("longName")
        return t, _clean(nm, t)
    except Exception:
        return t, t


def main() -> None:
    today = datetime.date.today().isoformat()
    tickers = sorted({_norm(t) for t in im.members_asof("us", today, n=600)})
    print(f"S&P500 {len(tickers)} 티커 — yfinance 이름 fetch…", flush=True)
    names: dict[str, str] = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for i, (t, nm) in enumerate(ex.map(_fetch, tickers), 1):
            names[t] = nm
            if i % 50 == 0:
                print(f"  {i}/{len(tickers)}", flush=True)
    ok = sum(1 for t, n in names.items() if n != t)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "sp500_seed.py")
    with open(out, "w", encoding="utf-8") as f:
        f.write('"""sp500_seed.py — S&P500(미국 시총 상위 ~500) 티커→영문명 시드 (표시·검색용).\n\n')
        f.write("scripts/gen_sp500_seed.py 로 생성(재현). 소스: providers.index_membership 현재구성\n")
        f.write("+ yfinance shortName. ticker_names.py 가 병합해 대시보드 검색 유니버스로 노출.\n")
        f.write(f"생성일 {today} · {len(names)}종목(이름확보 {ok}).\n")
        f.write('"""\n')
        f.write("from __future__ import annotations\n\n")
        f.write("SP500: dict[str, str] = {\n")
        for t in sorted(names):
            nm = names[t].replace("\\", "").replace('"', "'")
            f.write(f'    "{t}": "{nm}",\n')
        f.write("}\n")
    print(f"wrote {out}: {len(names)} tickers, {ok} named", flush=True)


if __name__ == "__main__":
    main()
