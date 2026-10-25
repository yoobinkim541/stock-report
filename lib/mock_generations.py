#!/usr/bin/env python3
"""
lib/mock_generations.py — 모의계좌 만기 리셋 시 성과 "세대" 분리.

키움 국내 모의투자는 3개월마다 만기 리셋되어 브로커 측 NAV·보유종목이
강제로 초기화된다. 리셋 이후에도 최초 인셉션 NAV 를 기준으로 누적수익률·
MDD 를 계속 계산하면 계좌 리셋이 전략의 대규모 손실처럼 보이는 왜곡이
생긴다 (NAV가 시드로 돌아가는데 인셉션은 몇 달 전 값 그대로라 cum_ret 이
갑자기 폭락하고 MDD 가 허수로 치솟음).

이 모듈은 리셋 시점을 히스토리 컬렉션 안에 "세대 경계" 레코드로 명시적으로
남겨, 리포트가 현재 세대 지표와 전체 누적 지표를 동시에 계산할 수 있게
한다. 마감된 세대의 최종 성과·보유종목은 `<collection>_generations` 에
별도 아카이브되고, 원장 스냅샷은 세대별로 이어붙여 전체 성과 곡선도
복원할 수 있다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

KST = timezone(timedelta(hours=9))

_BOUNDARY_KIND = "generation_boundary"
_SNAPSHOT_KIND = "snapshot"
# 세대 마감 시 조회한 최종 NAV 관측점. 원장에 별도 행으로 저장되지 않고, 경계 마커의
# final_nav(구 데이터는 `<collection>_generations` 아카이브)에서 세대 시계열 끝에 붙인다.
_CLOSE_KIND = "generation_close"


def _store():
    import store as _s
    return _s


def _latest_boundary_date(rows: list[dict]) -> str | None:
    boundaries = [str(r["date"]) for r in rows if r.get("kind") == _BOUNDARY_KIND and r.get("date")]
    return max(boundaries) if boundaries else None


def active_snapshots(collection: str, *, store_module=None) -> list[dict]:
    """현재 세대(가장 최근 경계 이후)의 NAV 스냅샷만 반환. 경계가 없으면 전체 히스토리."""
    store_module = store_module or _store()
    segments = generation_segments(collection, store_module=store_module)
    if not segments:
        return []
    return list((segments[-1] or {}).get("snapshots") or [])


def generation_count(collection: str, *, store_module=None) -> int:
    """지금까지 마감된 세대 수 + 1 (현재 진행 중인 세대 포함)."""
    store_module = store_module or _store()
    rows = store_module.all(collection)
    closed = sum(1 for r in rows if r.get("kind") == _BOUNDARY_KIND)
    return closed + 1


def _closing_point(snaps: list[dict], *, final_nav, date, generation) -> dict | None:
    """마감 NAV 를 세대 시계열에 정확히 한 번 반영할 관측점.

    마지막 snapshot NAV 와 같으면 이미 시계열에 반영된 값이므로 None (중복 점 없음).
    """
    if final_nav is None:
        return None
    nav = float(final_nav)
    if snaps and float(snaps[-1]["nav"]) == nav:
        return None
    return {"date": date, "kind": _CLOSE_KIND, "generation": generation, "nav": nav}


def _closed_series(snaps: list[dict], *, final_nav, date, generation) -> list[dict]:
    """마감된 세대의 NAV 시계열 = 세대 snapshot + 마감 NAV(필요할 때만). 마감 요약과 롤업이 공용."""
    point = _closing_point(snaps, final_nav=final_nav, date=date, generation=generation)
    return list(snaps) + ([point] if point else [])


def _boundary_final_nav(boundary: dict, archive_lookup: Callable[[int], dict | None]):
    """경계 마커의 마감 NAV. 구 형식 마커(final_nav 없음)는 같은 세대의 아카이브에서 읽는다."""
    if boundary.get("final_nav") is not None:
        return boundary.get("final_nav")
    try:
        gen = int(boundary.get("generation"))
    except (TypeError, ValueError):
        return None
    archived = archive_lookup(gen)
    return (archived or {}).get("final_nav")


def generation_segments(collection: str, *, store_module=None) -> list[dict]:
    """히스토리 원장을 세대 단위로 분리해 반환한다.

    반환 형식: [{"generation": 1, "snapshots": [...]}, ...]
    마감된 세대의 snapshots 끝에는 마감 NAV 관측점(kind=generation_close)이 한 번
    붙는다(마지막 snapshot 과 같은 값이면 생략). 스냅샷이 없는 세대(중복 마감)는
    마감점도 붙이지 않는다. 마지막 세대는 스냅샷이 아직 없더라도 빈 리스트로 포함한다.
    """
    store_module = store_module or _store()
    rows = store_module.all(collection)

    archive_cache: dict[int, dict] | None = None

    def archive_lookup(gen: int) -> dict | None:
        nonlocal archive_cache
        if archive_cache is None:
            archive_cache = {}
            for rec in store_module.all(f"{collection}_generations"):
                if rec.get("final_nav") is None:
                    continue
                try:
                    archive_cache[int(rec.get("generation"))] = rec
                except (TypeError, ValueError):
                    continue
        return archive_cache.get(gen)

    segments: list[dict] = []
    current: list[dict] = []
    generation = 1
    for row in rows:
        kind = row.get("kind")
        if kind == _BOUNDARY_KIND:
            if current:
                current = _closed_series(
                    current,
                    final_nav=_boundary_final_nav(row, archive_lookup),
                    date=str(row.get("date") or ""),
                    generation=generation,
                )
            segments.append({"generation": generation, "snapshots": current})
            current = []
            try:
                generation = int(row.get("generation") or generation) + 1
            except (TypeError, ValueError):
                generation += 1
            continue
        if kind == _SNAPSHOT_KIND and row.get("nav") is not None:
            current.append(dict(row))

    segments.append({"generation": generation, "snapshots": current})
    return segments


def _segment_summary(snaps: list[dict], *, max_drawdown_fn) -> dict:
    if not snaps:
        return {
            "start_date": None,
            "end_date": None,
            "n_snapshots": 0,
            "inception_nav": None,
            "final_nav": None,
            "cum_return_pct": None,
            "mdd_pct": None,
            "nav_series": [],
        }
    inception_nav = float(snaps[0]["nav"])
    final_nav = float(snaps[-1]["nav"])
    nav_series = [float(s["nav"]) for s in snaps]
    mdd_pct = max_drawdown_fn(nav_series) * 100.0 if len(nav_series) >= 2 else 0.0
    cum_return_pct = (final_nav / inception_nav - 1.0) * 100.0 if inception_nav else 0.0
    return {
        "start_date": str(snaps[0].get("date") or "")[:10] or None,
        "end_date": str(snaps[-1].get("date") or "")[:10] or None,
        "n_snapshots": len(snaps),
        "inception_nav": inception_nav,
        "final_nav": final_nav,
        "cum_return_pct": cum_return_pct,
        "mdd_pct": mdd_pct,
        "nav_series": nav_series,
    }


def generation_rollup(collection: str, *, store_module=None,
                      max_drawdown_fn: Callable[[list], float] | None = None) -> dict:
    """현재 세대 + 전체 세대를 함께 요약한다.

    current: active_snapshots() 기준 현재 세대 지표
    lifetime: 세대별 NAV 곡선을 이어붙인 전체 지표
    """
    store_module = store_module or _store()
    if max_drawdown_fn is None:
        from ml.adaptive import reward as _reward
        max_drawdown_fn = _reward.max_drawdown

    segments = generation_segments(collection, store_module=store_module)
    current = active_snapshots(collection, store_module=store_module)
    current_summary = _segment_summary(current, max_drawdown_fn=max_drawdown_fn)
    current_summary["generation"] = generation_count(collection, store_module=store_module)
    if current_summary["nav_series"]:
        prev_nav = current_summary["nav_series"][-2] if len(current_summary["nav_series"]) >= 2 else None
        current_summary["nav"] = current_summary["final_nav"]
        current_summary["day_return_pct"] = (
            (current_summary["final_nav"] / prev_nav - 1.0) * 100.0 if prev_nav else 0.0
        )
    else:
        current_summary["nav"] = None
        current_summary["day_return_pct"] = None

    lifetime_series: list[dict] = []
    lifetime_base_nav: float | None = None
    growth = 1.0
    lifetime_start: str | None = None
    for seg in segments:
        snaps = seg.get("snapshots") or []
        if not snaps:
            continue
        seg_base = float(snaps[0]["nav"])
        if seg_base <= 0:
            continue
        if lifetime_base_nav is None:
            lifetime_base_nav = seg_base
            lifetime_start = str(snaps[0].get("date") or "")[:10] or None
        for snap in snaps:
            stitched_nav = lifetime_base_nav * growth * (float(snap["nav"]) / seg_base)
            lifetime_series.append({
                "date": str(snap.get("date", "")),
                "generation": seg.get("generation"),
                "nav": stitched_nav,
                "raw_nav": float(snap["nav"]),
            })
        growth *= float(snaps[-1]["nav"]) / seg_base

    if lifetime_series and lifetime_base_nav is not None:
        lifetime_navs = [float(r["nav"]) for r in lifetime_series]
        lifetime_summary = {
            "generation": generation_count(collection, store_module=store_module),
            "start_date": lifetime_start,
            "end_date": str(lifetime_series[-1]["date"])[:10],
            "n_snapshots": len(lifetime_series),
            "inception_nav": lifetime_base_nav,
            "final_nav": lifetime_navs[-1],
            "cum_return_pct": (lifetime_navs[-1] / lifetime_base_nav - 1.0) * 100.0,
            "mdd_pct": max_drawdown_fn(lifetime_navs) * 100.0 if len(lifetime_navs) >= 2 else 0.0,
            "nav_series": lifetime_series,
        }
    else:
        lifetime_summary = {
            "generation": generation_count(collection, store_module=store_module),
            "start_date": None,
            "end_date": None,
            "n_snapshots": 0,
            "inception_nav": None,
            "final_nav": None,
            "cum_return_pct": None,
            "mdd_pct": None,
            "nav_series": [],
        }

    return {
        "generation_count": generation_count(collection, store_module=store_module),
        "segments": segments,
        "current": current_summary,
        "lifetime": lifetime_summary,
    }


def close_generation(
    collection: str,
    *,
    get_balance_fn: Callable[[], dict],
    reason: str = "계좌 리셋",
    max_drawdown_fn: Callable[[list], float] | None = None,
    store_module=None,
    now: str | None = None,
) -> dict:
    """현재 세대를 마감: 히스토리에 세대 경계 마커(마감 NAV·요약 포함)를 남기고
    성과 요약을 `<collection>_generations` 에 아카이브한다.

    마감 요약은 generation_rollup 과 같은 `_closed_series`(세대 snapshot + 마감 NAV)를
    소비하므로 마감 NAV 가 세대·전체 수익률/MDD 에 정확히 한 번 반영된다.

    경계 마커를 먼저 쓴다(롤업의 진실원). 아카이브 쓰기 전에 실패했다면 재실행 시
    경계 마커에서 아카이브를 복구한다. 이미 마감되어 새 snapshot 이 없는 세대를 다시
    마감하려 하면(중복 마감) 아무것도 쓰지 않고 예외를 던진다.

    호출 시점 주의: 만료 예정 계좌의 App Key/Secret 을 새 계좌 것으로 교체하기
    **전**에 실행해야 실제 보유종목·NAV 를 정확히 캡처한다. 잔고 조회가
    실패하면(이미 계좌가 종료된 상태) 예외를 던진다 — 마감은 계좌가 아직
    살아있을 때만 유효하다.
    """
    store_module = store_module or _store()
    if max_drawdown_fn is None:
        from ml.adaptive import reward as _reward
        max_drawdown_fn = _reward.max_drawdown

    archive_name = f"{collection}_generations"
    snaps = active_snapshots(collection, store_module=store_module)
    gen_no = generation_count(collection, store_module=store_module)
    if not snaps and gen_no > 1:
        return _resume_or_reject_duplicate_close(collection, archive_name, gen_no - 1,
                                                 store_module=store_module)

    bal = get_balance_fn() or {}
    if not bal.get("ok"):
        raise RuntimeError(
            "잔고 조회 실패 — 계좌가 아직 살아있는 상태(구 앱키)에서 마감을 실행해야 합니다"
        )

    now = now or datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    final_nav = bal.get("nav")
    if final_nav is None:
        final_nav = (bal.get("pos_value") or 0.0) + (bal.get("cash_krw") or 0.0)
    final_nav = float(final_nav)

    series = _closed_series(snaps, final_nav=final_nav, date=now, generation=gen_no)
    seg = _segment_summary(series, max_drawdown_fn=max_drawdown_fn)

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

    summary = {
        "date": now,
        "generation": gen_no,
        "reason": reason,
        "start_date": seg["start_date"] or now[:10],
        "end_date": now[:10],
        "n_snapshots": len(snaps),
        "inception_nav": seg["inception_nav"],
        "final_nav": final_nav,
        "cum_return_pct": seg["cum_return_pct"],
        "mdd_pct": seg["mdd_pct"],
        "holdings_at_close": holdings_at_close,
    }

    store_module.append(collection, {**summary, "kind": _BOUNDARY_KIND})
    store_module.append(archive_name, summary)
    return summary


def _resume_or_reject_duplicate_close(collection: str, archive_name: str, closed_gen: int,
                                      *, store_module) -> dict:
    """현재 세대에 snapshot 이 없는데 마감이 다시 요청된 경우.

    직전 마감이 경계 마커만 쓰고 아카이브 쓰기 전에 실패했다면 마커의 요약으로 아카이브를
    복구해 반환한다. 아카이브까지 끝난 마감이면 중복 마감이므로 쓰기 없이 예외.
    """
    archived = [r for r in store_module.all(archive_name) if r.get("generation") == closed_gen]
    if archived:
        raise RuntimeError(
            f"세대 {closed_gen} 은 이미 마감됨 — 마감 후 새 snapshot 이 없어 중복 마감을 거부합니다"
        )
    boundaries = [r for r in store_module.all(collection)
                  if r.get("kind") == _BOUNDARY_KIND and r.get("generation") == closed_gen]
    marker = boundaries[-1] if boundaries else {}
    if marker.get("final_nav") is None:
        raise RuntimeError(
            f"세대 {closed_gen} 경계에 마감 요약이 없어 아카이브를 복구할 수 없습니다 — 중복 마감 거부"
        )
    summary = {k: v for k, v in marker.items() if k != "kind"}
    store_module.append(archive_name, summary)
    return summary
