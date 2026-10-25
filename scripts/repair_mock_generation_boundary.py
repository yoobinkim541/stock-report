#!/usr/bin/env python3
"""scripts/repair_mock_generation_boundary.py — 모의 원장(<market>_mock_history) 세대 경계·무효 snapshot 복구.

기존 기록부가 남긴 오염을 원장 규칙(lib/mock_generations)에 맞게 정리한다.

  1) 무효 snapshot 표시(삭제 안 함): 잔고 실패로 nav=None/0/비유한 → invalid="nav_missing",
     현금 결측·0 + 보유 있음 + 직전 유효 NAV 대비 급변(반쪽 잔고) → invalid="incomplete_balance".
  2) 현재 세대(마지막 경계 이후)에서 모의계좌 리셋(보유 0 + NAV≈설정 초기자본 + 직전 유효 NAV 와
     크게 다름)을 찾아 그 행 **앞에** 세대 경계를 삽입하고(마감 NAV = 직전 유효 NAV) 마감 요약을
     `<collection>_generations` 에 아카이브한다. 리셋 행의 현금이 결측이면 NAV 가 관측값이 아니라
     시드 폴백이므로 invalid="unobserved_nav" 로 표시한다.
  3) 멱등: 이미 표시된 행·삽입된 경계는 다시 바꾸지 않는다. 경계만 쓰이고 아카이브가 빠진
     경우(중단)는 아카이브만 보충한다. 변경할 것이 없으면 아무것도 쓰지 않는다.

사용:
  # 계획만 출력 (기본 — 쓰기 없음)
  python scripts/repair_mock_generation_boundary.py --collection us_mock_history
  # 적용 — store DB 백업(SQLite backup API) 후 쓰기
  python scripts/repair_mock_generation_boundary.py --collection us_mock_history --apply --confirm

옵션: --seed (기본: KOREA_MOCK_SEED / KIWOOM_MOCK_SEED env, 없으면 100000 / 10000000)
      --backup-dir (기본 ~/backups/stock-report/sqlite) · --json (계획을 JSON 으로 출력)
적용 전 크론(us/kr mock track·report)이 돌지 않는 시간에 실행한다. 계획 이후 원장이 바뀌면 쓰지 않고 중단.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import mock_generations as mg  # noqa: E402

_SEED_ENV = {
    "us_mock_history": ("KOREA_MOCK_SEED", 100_000.0),
    "kr_mock_history": ("KIWOOM_MOCK_SEED", 10_000_000.0),
}
_REPAIR_TAG = "repair_mock_generation_boundary"
_DEFAULT_BACKUP_DIR = Path.home() / "backups" / "stock-report" / "sqlite"


def default_seed(collection: str) -> float | None:
    env, default = _SEED_ENV.get(collection, (None, None))
    if env is None:
        return None
    try:
        return float(os.getenv(env, str(default)))
    except ValueError:
        return default


def _positions(row: dict) -> int:
    try:
        return int(row.get("positions") or 0)
    except (TypeError, ValueError):
        return 0


def plan_repair(rows: list[dict], archive: list[dict], *, collection: str, seed) -> dict:
    """원장 행 → 복구 계획 (순수 함수, 쓰기 없음).

    반환 {"marks": [{index, date, reason, nav, cash}], "boundary": {index, row} | None,
          "archive": summary | None, "rows": n}
    """
    marks: list[dict] = []
    marked: dict[int, str] = {}

    def mark(i: int, reason: str) -> None:
        row = rows[i]
        if row.get("invalid") or i in marked:
            return
        marked[i] = reason
        marks.append({"index": i, "date": row.get("date"), "reason": reason,
                      "nav": row.get("nav"), "cash": row.get("cash")})

    boundary_idx = [i for i, r in enumerate(rows) if r.get("kind") == mg.BOUNDARY_KIND]
    last_boundary = boundary_idx[-1] if boundary_idx else -1
    generation = len(boundary_idx) + 1

    # 한 번의 순방향 패스: 세대별 직전 유효 NAV 추적 → 무효 표시, 현재 세대에서는 첫 리셋 지점 탐지
    boundary = None
    summary = None
    prev_nav, prev_row = None, None
    current_snaps: list[dict] = []
    for i, row in enumerate(rows):
        kind = row.get("kind")
        if kind == mg.BOUNDARY_KIND:
            prev_nav, prev_row = None, None
            continue
        if kind != mg.SNAPSHOT_KIND or row.get("invalid"):
            continue
        nav = mg.positive_float(row.get("nav"))
        if nav is None:
            mark(i, "nav_missing")
            continue
        if mg.looks_incomplete(nav, row.get("cash"), _positions(row), prev_nav):
            mark(i, "incomplete_balance")
            continue
        if (boundary is None and i > last_boundary
                and mg.looks_like_reset(nav, _positions(row), prev_nav, seed)):
            summary = mg.build_close_summary(
                current_snaps, generation=generation, final_nav=prev_nav,
                now=str(row.get("date") or ""), reason="모의계좌 리셋 감지 (원장 복구)",
                holdings_at_close=[],
                extra={"close_basis": "last_valid_snapshot",
                       "close_nav_date": str(prev_row.get("date") or "") or None,
                       "holdings_unknown": True, "repaired_by": _REPAIR_TAG})
            boundary = {"index": i, "row": {**summary, "kind": mg.BOUNDARY_KIND}}
            if row.get("cash") is None:
                # 현금이 관측되지 않은 시드 NAV = 잔고 실패 시 시드 폴백값 → 새 세대 첫 기록이 아니다
                mark(i, "unobserved_nav")
                prev_nav, prev_row = None, None
                continue
        prev_nav, prev_row = nav, row
        if i > last_boundary and boundary is None:
            current_snaps.append({**row, "nav": nav})

    # 경계는 있는데 아카이브가 빠진 복구 경계(중단된 적용) → 아카이브만 보충
    if boundary is None and last_boundary >= 0:
        marker = rows[last_boundary]
        archived_gens = {r.get("generation") for r in archive}
        if marker.get("repaired_by") == _REPAIR_TAG and marker.get("generation") not in archived_gens:
            summary = {k: v for k, v in marker.items() if k != "kind"}

    return {"collection": collection, "seed": seed, "rows": len(rows),
            "marks": marks, "boundary": boundary, "archive": summary}


def plan_has_changes(plan: dict) -> bool:
    return bool(plan["marks"] or plan["boundary"] or plan["archive"])


def apply_plan_to_rows(rows: list[dict], plan: dict) -> list[dict]:
    reasons = {m["index"]: m["reason"] for m in plan["marks"]}
    out: list[dict] = []
    insert_at = (plan["boundary"] or {}).get("index")
    for i, row in enumerate(rows):
        if i == insert_at:
            out.append(dict(plan["boundary"]["row"]))
        out.append({**row, "invalid": reasons[i]} if i in reasons else dict(row))
    return out


def backup_store(store_module, backup_dir: Path, *, collection: str, counts: dict) -> Path:
    """SQLite backup API 로 store DB 일관 사본 + manifest 를 남긴다. 반환: 백업 디렉터리."""
    src_path = Path(store_module.db_path())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = Path(backup_dir) / stamp
    target.mkdir(parents=True, exist_ok=False)
    dest_path = target / src_path.name
    src = sqlite3.connect(str(src_path))
    try:
        dest = sqlite3.connect(str(dest_path))
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()
    check = sqlite3.connect(str(dest_path))
    try:
        if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError(f"백업 무결성 검사 실패: {dest_path}")
    finally:
        check.close()
    manifest = {"created_utc": stamp, "source_db": str(src_path), "backup_db": str(dest_path),
                "tool": _REPAIR_TAG, "collection": collection, "counts_before": counts}
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return target


def apply_repair(collection: str, *, seed, backup_dir: Path, store_module=None) -> dict:
    """계획 → 백업 → 쓰기 → 행 수 검증. 변경이 없으면 백업·쓰기 모두 생략."""
    if store_module is None:
        import store as store_module
    archive_name = f"{collection}_generations"
    rows = store_module.all(collection)
    archive = store_module.all(archive_name)
    plan = plan_repair(rows, archive, collection=collection, seed=seed)
    if not plan_has_changes(plan):
        return {"plan": plan, "applied": False, "backup": None}

    counts = {collection: len(rows), archive_name: len(archive)}
    backup = backup_store(store_module, backup_dir, collection=collection, counts=counts)

    if len(store_module.all(collection)) != len(rows):
        raise RuntimeError(f"계획 이후 {collection} 원장이 변경됨 — 쓰기 중단, 재실행 필요 (백업 {backup})")
    if plan["marks"] or plan["boundary"]:
        store_module.replace_all(collection, apply_plan_to_rows(rows, plan))
    if plan["archive"]:
        store_module.append(archive_name, plan["archive"])

    expect_rows = len(rows) + (1 if plan["boundary"] else 0)
    expect_archive = len(archive) + (1 if plan["archive"] else 0)
    got_rows, got_archive = len(store_module.all(collection)), len(store_module.all(archive_name))
    if got_rows != expect_rows or got_archive != expect_archive:
        raise RuntimeError(
            f"적용 후 행 수 불일치 ({collection} {got_rows}≠{expect_rows}, "
            f"{archive_name} {got_archive}≠{expect_archive}) — 백업 {backup} 확인")
    return {"plan": plan, "applied": True, "backup": str(backup)}


def describe(plan: dict) -> list[str]:
    lines = [f"컬렉션 {plan['collection']} · 행 {plan['rows']} · 시드 {plan['seed']}"]
    if not plan_has_changes(plan):
        lines.append("변경 없음 (이미 정리됨)")
        return lines
    for m in plan["marks"]:
        lines.append(f"  무효 표시 [{m['index']}] {m['date']} nav={m['nav']} cash={m['cash']} → {m['reason']}")
    if plan["boundary"]:
        b = plan["boundary"]["row"]
        lines.append(f"  세대 경계 삽입 [{plan['boundary']['index']} 앞] {b['date']} · 세대 {b['generation']} 마감 "
                     f"NAV {b['final_nav']:,.2f} ({b.get('close_nav_date')}) · 누적 "
                     f"{b['cum_return_pct']:+.2f}% · MDD {b['mdd_pct']:.2f}%")
    if plan["archive"]:
        lines.append(f"  아카이브 추가 → {plan['collection']}_generations (세대 {plan['archive']['generation']})")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="모의 원장 세대 경계·무효 snapshot 복구 (기본 dry-run)")
    parser.add_argument("--collection", required=True, choices=sorted(_SEED_ENV))
    parser.add_argument("--seed", type=float, default=None, help="설정 초기자본 (기본: env)")
    parser.add_argument("--apply", action="store_true", help="계획을 실제로 적용")
    parser.add_argument("--confirm", action="store_true", help="--apply 와 함께 필수")
    parser.add_argument("--backup-dir", default=str(_DEFAULT_BACKUP_DIR))
    parser.add_argument("--json", action="store_true", help="계획을 JSON 으로 출력")
    args = parser.parse_args(argv)
    seed = args.seed if args.seed is not None else default_seed(args.collection)

    if args.apply and not args.confirm:
        print("--apply 는 --confirm 과 함께 실행해야 합니다 (먼저 dry-run 으로 계획 확인)", file=sys.stderr)
        return 2

    import store
    if not args.apply:
        archive = store.all(f"{args.collection}_generations")
        plan = plan_repair(store.all(args.collection), archive, collection=args.collection, seed=seed)
        print(json.dumps(plan, ensure_ascii=False, indent=1, default=str) if args.json
              else "\n".join(["[DRY-RUN] 쓰기 없음"] + describe(plan)))
        return 0

    result = apply_repair(args.collection, seed=seed, backup_dir=Path(args.backup_dir), store_module=store)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    else:
        print("\n".join(describe(result["plan"])))
        print(f"적용 완료 · 백업 {result['backup']}" if result["applied"] else "적용할 변경 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
