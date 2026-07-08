"""lib/viewer_interest.py — 대시보드에서 '지금 보는 종목'을 실시간 스트림에 자동 편입.

종목분석 페이지가 record()로 관심 심볼을 기록하면 kis_stream.compute_watchlist 가
recent()를 최후미 우선순위로 편입(90초 재구독 주기) — 조회 중인 KR 종목이 ~1.5분 내
틱단위 실시간 호가로 승격된다. KIS 등록한도(41) 내 잔여 슬롯만 사용(기존 우선순위 불변).
"""
from __future__ import annotations

import json
import os
import time

_PATH = os.path.expanduser("~/.cache/viewer_interest.json")
MAX_AGE_S = 600          # 10분 미조회 시 자연 해제 (슬롯 반납)
MAX_SYMS = 3             # 동시 관심 상한 (슬롯 예산 보호)


def record(symbol: str) -> None:
    """관심 심볼 갱신 (base 표기 권장). 실패 무해."""
    s = (symbol or "").strip().upper()
    if not s:
        return
    try:
        data = {}
        try:
            with open(_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
        now = time.time()
        data = {k: v for k, v in data.items() if now - float(v) < MAX_AGE_S}
        data[s] = now
        if len(data) > MAX_SYMS:                 # 오래된 것부터 정리
            for k in sorted(data, key=data.get)[:len(data) - MAX_SYMS]:
                data.pop(k, None)
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _PATH)
    except Exception:
        pass


def recent(max_age_s: int = MAX_AGE_S) -> list[str]:
    """최근 조회 심볼 (오래된 것 제외). 실패 시 []."""
    try:
        with open(_PATH, encoding="utf-8") as f:
            data = json.load(f)
        now = time.time()
        return sorted((k for k, v in data.items() if now - float(v) < max_age_s),
                      key=lambda k: -data[k])
    except Exception:
        return []
