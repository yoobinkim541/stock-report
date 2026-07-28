#!/usr/bin/env python3
"""
lib/mock_generations.py — 모의계좌 만기 리셋 시 성과 "세대" 분리.

키움 국내 모의투자는 3개월마다 만기 리셋되어 브로커 측 NAV·보유종목이
강제로 초기화된다. 리셋 이후에도 최초 인셉션 NAV 를 기준으로 누적수익률·
MDD 를 계속 계산하면 계좌 리셋이 전략의 대규모 손실처럼 보이는 왜곡이
생긴다 (NAV가 시드로 돌아가는데 인셉션은 몇 달 전 값 그대로라 cum_ret 이
갑자기 폭락하고 MDD 가 허수로 치솟음).

이 모듈은 리셋 시점을 히스토리 컬렉션 안에 "세대 경계" 레코드로 명시적으로
남겨, 리포트가 현재 세대의 스냅샷만으로 누적수익률·MDD 를 계산하도록
한다. 마감된 세대의 최종 성과·보유종목은 `<collection>_generations` 에
별도 아카이브된다 (연속곡선으로 이어붙이지 않고 세대별로 분리 보존).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

KST = timezone(timedelta(hours=9))

_BOUNDARY_KIND = "generation_boundary"
_SNAPSHOT_KIND = "snapshot"


def _store():
    import store as _s
    return _s


def _latest_boundary_date(rows: list[dict]) -> str | None:
    boundaries = [str(r["date"]) for r in rows if r.get("kind") == _BOUNDARY_KIND and r.get("date")]
    return max(boundaries) if boundaries else None


def active_snapshots(collection: str, *, store_module=None) -> list[dict]:
    """현재 세대(가장 최근 경계 이후)의 NAV 스냅샷만 반환. 경계가 없으면 전체 히스토리."""
    store_module = store_module or _store()
    rows = store_module.all(collection)
    boundary = _latest_boundary_date(rows)
    snaps = [r for r in rows if r.get("kind") == _SNAPSHOT_KIND and r.get("nav") is not None]
    if boundary:
        snaps = [r for r in snaps if str(r.get("date", "")) > boundary]
    return snaps


def generation_count(collection: str, *, store_module=None) -> int:
    """지금까지 마감된 세대 수 + 1 (현재 진행 중인 세대 포함)."""
    store_module = store_module or _store()
    rows = store_module.all(collection)
    closed = sum(1 for r in rows if r.get("kind") == _BOUNDARY_KIND)
    return closed + 1


def close_generation(
    collection: str,
    *,
    get_balance_fn: Callable[[], dict],
    reason: str = "계좌 리셋",
    max_drawdown_fn: Callable[[list], float] | None = None,
    store_module=None,
    now: str | None = None,
) -> dict:
    """현재 세대를 마감: 성과 요약을 `<collection>_generations` 에 아카이브하고
    히스토리에 세대 경계 마커를 남긴다.

    호출 시점 주의: 만료 예정 계좌의 App Key/Secret 을 새 계좌 것으로 교체하기
    **전**에 실행해야 실제 보유종목·NAV 를 정확히 캡처한다. 잔고 조회가
    실패하면(이미 계좌가 종료된 상태) 예외를 던진다 — 마감은 계좌가 아직
    살아있을 때만 유효하다.
    """
    store_module = store_module or _store()
    if max_drawdown_fn is None:
        from ml.adaptive import reward as _reward
        max_drawdown_fn = _reward.max_drawdown

    snaps = active_snapshots(collection, store_module=store_module)
    bal = get_balance_fn() or {}
    if not bal.get("ok"):
        raise RuntimeError(
            "잔고 조회 실패 — 계좌가 아직 살아있는 상태(구 앱키)에서 마감을 실행해야 합니다"
        )

    now = now or datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    final_nav = bal.get("nav")
    if final_nav is None:
        final_nav = (bal.get("pos_value") or 0.0) + (bal.get("cash_krw") or 0.0)

    inception_nav = float(snaps[0]["nav"]) if snaps else float(final_nav)
    nav_series = [float(s["nav"]) for s in snaps] + [float(final_nav)]
    mdd_pct = max_drawdown_fn(nav_series) * 100.0
    cum_return_pct = (final_nav / inception_nav - 1.0) * 100.0 if inception_nav else 0.0

    holdings_at_close = [
        {
            "code": code,
            "name": p.get("name"),
            "shares": p.get("shares"),
            "value": p.get("value"),
        }
        for code, p in (bal.get("positions") or {}).items()
        if int(p.get("shares", 0) or 0) > 0
    ]

    gen_no = generation_count(collection, store_module=store_module)
    summary = {
        "date": now,
        "generation": gen_no,
        "reason": reason,
        "start_date": str(snaps[0]["date"])[:10] if snaps else now[:10],
        "end_date": now[:10],
        "n_snapshots": len(snaps),
        "inception_nav": inception_nav,
        "final_nav": final_nav,
        "cum_return_pct": cum_return_pct,
        "mdd_pct": mdd_pct,
        "holdings_at_close": holdings_at_close,
    }

    store_module.append(f"{collection}_generations", summary)
    store_module.append(collection, {
        "date": now, "kind": _BOUNDARY_KIND, "generation": gen_no, "reason": reason,
    })
    return summary
