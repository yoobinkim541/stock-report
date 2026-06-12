#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store_smoke_test.py — SQLite store + 마이그레이션 스모크 테스트 (네트워크 불필요)

격리된 임시 DB(STOCK_REPORT_DB)에서 store API와 각 기록로그 모듈의
마이그레이션 경로를 검증한다.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "✅"
FAIL = "❌"
_results: list[tuple[bool, str]] = []


def check(cond: bool, label: str):
    _results.append((bool(cond), label))
    print(f"{PASS if cond else FAIL} {label}")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="store_test_")
    os.environ["STOCK_REPORT_DB"] = os.path.join(tmp, "test.db")

    # store는 환경변수 설정 후 import (db_path 평가 시점 무관하지만 명시적으로)
    import importlib
    import store
    importlib.reload(store)

    # ── 1. 컬렉션 기본 API ─────────────────────────────────────────────
    store.append("alpha", {"v": 1})
    store.append("alpha", {"v": 2})
    rows = store.all("alpha")
    check(rows == [{"v": 1}, {"v": 2}], "컬렉션 append/all 순서 보존")
    check(store.count("alpha") == 2, "컬렉션 count")

    # 사용자 격리
    store.append("alpha", {"v": 99}, user="user2")
    check(store.all("alpha") == [{"v": 1}, {"v": 2}], "user 스코프: 기본 사용자 격리")
    check(store.all("alpha", user="user2") == [{"v": 99}], "user 스코프: user2 격리")

    # replace_all (삭제·수정)
    store.replace_all("alpha", [{"v": 2}])
    check(store.all("alpha") == [{"v": 2}], "replace_all 전체 교체")

    # 빈 컬렉션
    check(store.all("empty_coll") == [], "빈 컬렉션은 빈 리스트")

    # ── 2. 문서 API ───────────────────────────────────────────────────
    check(store.get_doc("cfg", {"d": True}) == {"d": True}, "get_doc 기본값")
    store.put_doc("cfg", {"x": 1})
    store.put_doc("cfg", {"x": 2})  # upsert
    check(store.get_doc("cfg") == {"x": 2}, "put_doc upsert")

    # ── 3. 레거시 JSON 마이그레이션 (멱등) ────────────────────────────
    legacy = Path(tmp) / "legacy_list.json"
    legacy.write_text(json.dumps([{"a": 1}, {"a": 2}]), encoding="utf-8")
    got = store.load_collection("legacy", legacy)
    check(got == [{"a": 1}, {"a": 2}], "레거시 JSON import")

    # 멱등: 두 번째 호출은 재import 안 함 (원본 변경해도 무시)
    legacy.write_text(json.dumps([{"a": 9}]), encoding="utf-8")
    got2 = store.load_collection("legacy", legacy)
    check(got2 == [{"a": 1}, {"a": 2}], "마이그레이션 멱등 (재import 안 함)")

    # 원본 파일 보존 (롤백 대비)
    check(legacy.exists(), "레거시 원본 파일 보존")

    # 존재하지 않는 레거시 → 빈 컬렉션 + migrated 마킹
    got3 = store.load_collection("nofile", Path(tmp) / "missing.json")
    check(got3 == [], "레거시 없으면 빈 컬렉션")

    # ── 4. 모듈 통합: tax_tracker ─────────────────────────────────────
    import tax_tracker
    importlib.reload(tax_tracker)
    rec = tax_tracker.add_sell("AAPL", 10, 100.0, 120.0, 1350.0)
    check(rec["gain_usd"] == 200.0, "tax_tracker add_sell 손익 계산")
    summary = tax_tracker.get_yearly_summary()
    check(summary["count"] == 1, "tax_tracker get_yearly_summary 반영")
    removed = tax_tracker.delete_record(1)
    check(removed is not None and tax_tracker.get_yearly_summary()["count"] == 0,
          "tax_tracker delete_record (1-based)")

    # ── 5. 모듈 통합: portfolio_tracker 배당 ──────────────────────────
    #  barbell_strategy(→numpy/pandas/yfinance) 의존 — 최소 환경에선 skip
    try:
        import portfolio_tracker
        importlib.reload(portfolio_tracker)
    except Exception as e:
        print(f"⏭️  portfolio_tracker skip (의존성 없음: {type(e).__name__})")
    else:
        portfolio_tracker.record_dividend(22.15, "ORCL", "테스트 배당")
        dsum = portfolio_tracker.get_dividend_summary()
        check(dsum["count"] == 1 and abs(dsum["total"] - 22.15) < 1e-6,
              "portfolio_tracker 배당 기록·집계")

    # ── 6. 문서 + 파일 미러 (Phase 2) ─────────────────────────────────
    mirror = Path(tmp) / "cfg_mirror.json"
    store.save_doc("wcfg", {"normal": {"A": 1.0}}, mirror)
    check(store.load_doc("wcfg", mirror) == {"normal": {"A": 1.0}}, "save_doc/load_doc 왕복")
    check(mirror.exists() and json.loads(mirror.read_text())["normal"]["A"] == 1.0,
          "save_doc 파일 미러 기록")

    # 외부(advisor 모사)가 미러 파일을 편집 → reimport로 store 반영
    mirror.write_text(json.dumps({"normal": {"A": 0.5, "B": 0.5}}), encoding="utf-8")
    check(store.reimport_doc("wcfg", mirror) is True, "reimport_doc 반환값")
    check(store.load_doc("wcfg", mirror)["normal"] == {"A": 0.5, "B": 0.5},
          "reimport_doc 외부 편집 반영")

    # 컬렉션 미러 + reimport
    cmir = Path(tmp) / "alerts_mirror.json"
    store.save_collection("alerts_x", [{"id": "1"}], cmir)
    check(json.loads(cmir.read_text()) == [{"id": "1"}], "save_collection 파일 미러")
    cmir.write_text(json.dumps([{"id": "1"}, {"id": "2"}]), encoding="utf-8")
    store.reimport_collection("alerts_x", cmir)
    check(store.all("alerts_x") == [{"id": "1"}, {"id": "2"}], "reimport_collection 반영")

    # 레거시 문서 자동 마이그레이션
    legacy_doc = Path(tmp) / "legacy_doc.json"
    legacy_doc.write_text(json.dumps({"x": 7}), encoding="utf-8")
    check(store.load_doc("ld", legacy_doc) == {"x": 7}, "load_doc 레거시 자동 마이그레이션")

    # ── 7. 모듈 통합: price_alerts (Phase 2) ──────────────────────────
    try:
        import bot.price_alerts as pa
        importlib.reload(pa)
    except Exception as e:
        print(f"⏭️  price_alerts skip (의존성 없음: {type(e).__name__})")
    else:
        pa.ALERTS_FILE = str(Path(tmp) / "price_alerts.json")  # 실제 설정 파일 보호
        aid = pa.add_alert("NVDA", 100.0, "buy", "테스트")
        loaded = pa.load_alerts()
        check(len(loaded) == 1 and loaded[0]["ticker"] == "NVDA",
              "price_alerts add/load (store 경유)")
        check(Path(pa.ALERTS_FILE).exists(), "price_alerts 파일 미러 생성")
        check(pa.remove_alert(aid) and pa.load_alerts() == [],
              "price_alerts remove")

    # ── 8. 모듈 통합: barbell_strategy 가중치 (Phase 2) ───────────────
    try:
        import barbell_strategy as bs
        importlib.reload(bs)
    except Exception as e:
        print(f"⏭️  barbell_strategy skip (의존성 없음: {type(e).__name__})")
    else:
        # 실제 프로젝트 설정 파일 미러를 tmp로 리다이렉트 (라이브 설정 보호)
        bs.DCA_WEIGHTS_FILE    = str(Path(tmp) / "dca_weights.json")
        bs.TARGET_WEIGHTS_FILE = str(Path(tmp) / "target_weights.json")
        bs.LEVERAGE_FILE       = str(Path(tmp) / "leverage_state.json")
        bs.save_dca_weights({"AAA": 0.6, "BBB": 0.4}, {"AAA": 1.0})
        n, b = bs.load_dca_weights()
        check(abs(n.get("AAA", 0) - 0.6) < 1e-6, "dca_weights store 왕복")
        check(Path(bs.DCA_WEIGHTS_FILE).exists(), "dca_weights 파일 미러")
        bs.save_leverage_state({"QLD": {"shares": 3.0, "avg_price_usd": 90.0, "updated": "x"}})
        lev = bs.load_leverage_state()
        check(lev["QLD"]["shares"] == 3.0 and "TQQQ" in lev,
              "leverage_state store 왕복 + 기본키 보강")
        bs.save_target_weights({"ORCL": 0.07})
        check(bs.load_target_weights().get("ORCL") == 0.07, "target_weights store 왕복")

    # ── 결과 ──────────────────────────────────────────────────────────
    n_fail = sum(1 for ok, _ in _results if not ok)
    total = len(_results)
    print("\n" + "━" * 40)
    print(f"  {total - n_fail}/{total} 통과")
    if n_fail:
        print(f"  {FAIL} {n_fail}건 실패")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
