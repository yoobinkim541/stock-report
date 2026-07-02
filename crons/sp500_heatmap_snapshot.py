#!/usr/bin/env python3
"""crons/sp500_heatmap_snapshot.py — S&P500 시장 맵 스냅샷 적재 (대시보드 즉시 로드용).

`dashboard.views._sp500_heatmap_live()` 라이브 조립(yf.download 503·~60초) →
`~/reports/ml-cache/sp500_heatmap.json` atomic 기록. 대시보드 `sp500_heatmap()` 이 이
스냅샷(<90분)을 우선 읽어 콜드로드를 즉시화. 매 20분 크론(장중 신선 유지).
표시 전용·주문 경로 없음.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    from dashboard.views import _HEATMAP_SNAP, _sp500_heatmap_live
    from safe_io import atomic_write_json
    rows = _sp500_heatmap_live()
    if rows:
        atomic_write_json(_HEATMAP_SNAP, rows)
        print(f"sp500_heatmap snapshot: {len(rows)} rows → {_HEATMAP_SNAP}")
    else:
        print("sp500_heatmap: empty rows (skip write)")


if __name__ == "__main__":
    main()
