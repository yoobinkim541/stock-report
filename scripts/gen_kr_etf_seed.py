#!/usr/bin/env python3
"""scripts/gen_kr_etf_seed.py — 루트 kr_etf_seed.py 생성(재현).

Naver 금융 ETF 목록 API(etfItemList — 전 KR ETF ~1,100+·EUC-KR) → 티커(.KS)→한글명 시드.
ticker_names 검색 유니버스에 병합 — 대시보드에서 KODEX/TIGER 등 국내 ETF 를
한글명·코드로 검색·분석(ETF 전용 뷰는 기존 kr_etf_summary 가 처리).

재실행(상장/폐지 갱신 시): uv run python scripts/gen_kr_etf_seed.py
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_URL = "https://finance.naver.com/api/sise/etfItemList.nhn"


def main() -> None:
    req = urllib.request.Request(_URL, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=20).read()
    data = json.loads(raw.decode("euc-kr", errors="replace"))
    items = (data.get("result") or {}).get("etfItemList") or []
    if len(items) < 300:
        sys.exit(f"ETF 목록 수집 실패({len(items)}) — 중단")
    etf = {}
    for it in items:
        code = str(it.get("itemcode") or "").strip()
        name = str(it.get("itemname") or "").strip()
        if len(code) == 6 and name:
            etf[f"{code}.KS"] = name
    today = datetime.date.today().isoformat()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "kr_etf_seed.py")
    with open(out, "w", encoding="utf-8") as f:
        f.write('"""kr_etf_seed.py — 국내 ETF 티커(.KS)→한글명 시드 (검색 유니버스·표시).\n\n')
        f.write("scripts/gen_kr_etf_seed.py 로 생성(재현·Naver etfItemList).\n")
        f.write(f"생성일 {today} · {len(etf)}종목.\n")
        f.write('"""\n')
        f.write("from __future__ import annotations\n\n")
        f.write("KR_ETF: dict[str, str] = {\n")
        for t in sorted(etf):
            nm = etf[t].replace('"', "'")
            f.write(f'    "{t}": "{nm}",\n')
        f.write("}\n")
    print(f"wrote {out}: {len(etf)} ETFs", flush=True)


if __name__ == "__main__":
    main()
