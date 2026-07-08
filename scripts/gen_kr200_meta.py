#!/usr/bin/env python3
"""scripts/gen_kr200_meta.py — 루트 kr200_meta.py 생성(재현).

KOSPI200 구성(Naver entryJongmok) × marcap 최신 시총·종목명 × Naver 업종(sise_group)
→ 루트 `kr200_meta.py` (NAME·SECTOR·MARKET_CAP). 코스피200 시장 맵(트리맵)용.
당일 등락률은 라이브/크론(dashboard.views.kr200_heatmap).

재실행(구성/시총 갱신 시): uv run python scripts/gen_kr200_meta.py  (Naver 네트워크·수분)
"""
from __future__ import annotations

import datetime
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.naver_kr import _get, kospi200_members


def upjong_sector_map() -> dict[str, str]:
    """Naver 업종 전체 페이지 순회 → {종목코드: 업종명}. EUC-KR."""
    idx = _get("https://finance.naver.com/sise/sise_group.naver?type=upjong").decode(
        "euc-kr", errors="replace")
    groups = re.findall(r'sise_group_detail\.naver\?type=upjong&no=(\d+)"[^>]*>([^<]+)<', idx)
    out: dict[str, str] = {}
    for no, name in groups:
        try:
            html = _get(
                f"https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={no}"
            ).decode("euc-kr", errors="replace")
            for code in re.findall(r"code=(\d{6})", html):
                out.setdefault(code, name.strip())
            time.sleep(0.15)                       # 예의상 스로틀
        except Exception as e:
            print(f"  업종 {name} 실패: {e}", flush=True)
    return out


def latest_marcap() -> dict[str, tuple[str, float]]:
    """올해 marcap parquet 최신일 → {코드: (종목명, 시총원)}."""
    from providers.kr_market_data import _marcap_year
    df = _marcap_year(datetime.date.today().year)
    last = df["Date"].max()
    day = df[df["Date"] == last]
    return {str(r["Code"]).zfill(6): (str(r["Name"]), float(r["Marcap"]))
            for _, r in day.iterrows()}


def main() -> None:
    members = kospi200_members()
    print(f"KOSPI200 구성 {len(members)}종목", flush=True)
    if len(members) < 150:
        sys.exit("구성 수집 실패(<150) — 중단")
    sectors = upjong_sector_map()
    print(f"업종 매핑 {len(sectors)}종목 수집", flush=True)
    mcap = latest_marcap()

    name, sector, cap = {}, {}, {}
    for c in members:
        nm, mc = mcap.get(c, ("", 0.0))
        if nm:
            name[c] = nm
        if mc > 0:
            cap[c] = mc
        sector[c] = sectors.get(c, "기타")

    today = datetime.date.today().isoformat()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "kr200_meta.py")
    with open(out, "w", encoding="utf-8") as f:
        f.write('"""kr200_meta.py — KOSPI200 종목명·업종·시총 스냅샷 (시장 맵 트리맵용·표시).\n\n')
        f.write("scripts/gen_kr200_meta.py 로 생성(재현) — Naver 구성/업종 + marcap 시총.\n")
        f.write(f"생성일 {today} · 구성 {len(members)}·업종 {len(sector)}·시총 {len(cap)}.\n")
        f.write('"""\n')
        f.write("from __future__ import annotations\n\n")
        f.write("NAME: dict[str, str] = {\n")
        for c in sorted(name):
            f.write(f'    "{c}": "{name[c]}",\n')
        f.write("}\n\n")
        f.write("SECTOR: dict[str, str] = {\n")
        for c in sorted(sector):
            f.write(f'    "{c}": "{sector[c]}",\n')
        f.write("}\n\n")
        f.write("MARKET_CAP: dict[str, float] = {\n")
        for c in sorted(cap):
            f.write(f'    "{c}": {cap[c]:.0f},\n')
        f.write("}\n")
    print(f"wrote {out}: name {len(name)}, sector {len(sector)}, cap {len(cap)}", flush=True)


if __name__ == "__main__":
    main()
