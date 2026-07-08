#!/usr/bin/env python3
"""crons/sp500_heatmap_snapshot.py — 시장 맵 3종 스냅샷 적재 (대시보드 즉시 로드용).

S&P500(yf 503 배치 ~60초) + 코스피200(yf 199 배치 ~30초) + 러셀2000 근사(NASDAQ
스크리너 1콜) → `~/reports/ml-cache/*_heatmap.json` atomic 기록. 대시보드가 스냅샷
(<90분)을 우선 읽어 콜드로드 즉시화. 매 20분 크론(장중 신선 유지). 한 맵 실패는
격리(나머지 계속). 표시 전용·주문 경로 없음.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    from dashboard import views
    from safe_io import atomic_write_json
    jobs = (("sp500", views._sp500_heatmap_live, views._HEATMAP_SNAP),
            ("kr200", views._kr200_heatmap_live, views._KR200_SNAP),
            ("russell2000", views._russell2000_live, views._RUSSELL_SNAP))
    for name, build, path in jobs:
        try:
            rows = build()
        except Exception as e:
            print(f"{name}_heatmap: 실패({e}) — 스킵")
            continue
        if rows:
            atomic_write_json(path, rows)
            print(f"{name}_heatmap snapshot: {len(rows)} rows → {path}")
        else:
            print(f"{name}_heatmap: empty rows (skip write)")


if __name__ == "__main__":
    main()
