"""test_repair_mock_generation_boundary.py — 운영 모의 원장 오염 복구 (합성 데이터, 격리 store DB).

운영 us_mock_history 에서 확인된 형태를 재현한다:
  - 2026-08-25/26 00:0x 비-EOD snapshot 이 cash=0.0 반쪽 잔고(NAV 절반)
  - 2026-10-13 00:03 잔고 실패 → 시드 폴백 NAV $100,000(현금 미관측·보유 0) snapshot, 세대 경계 없음
  - 이후 EOD snapshot 이 nav=None, cash=None
"""
import os
import sqlite3
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pytest  # noqa: E402

COL = "us_mock_history"


def _snap(date, nav, cash, positions, at=None):
    row = {"date": date, "kind": "snapshot", "nav": nav, "cash": cash, "positions": positions}
    if at:
        row["at"] = at
    return row


_LEDGER = [
    _snap("2026-06-01 00:03", 250_000.0, 30_000.0, 5),
    _snap("2026-06-01 06:40", 251_000.0, 30_000.0, 5, at="eod"),
    {"date": "2026-06-02 00:03", "kind": "cost", "cost": 12.5, "notional": 5_000.0},
    _snap("2026-08-24 06:40", 255_000.0, 120_000.0, 5, at="eod"),
    _snap("2026-08-25 00:03", 128_000.0, 0.0, 5),                    # 반쪽 잔고 (c)
    _snap("2026-08-25 06:40", 256_000.0, 120_000.0, 5, at="eod"),
    _snap("2026-08-26 00:04", 129_000.0, 0.0, 5),                    # 반쪽 잔고 (c)
    _snap("2026-08-26 06:40", 258_000.0, 120_000.0, 5, at="eod"),
    _snap("2026-10-10 06:40", 262_224.57, 125_000.0, 5, at="eod"),
    _snap("2026-10-13 00:03", 100_000.0, None, 0),                   # 시드 폴백 '리셋' (a)
    _snap("2026-10-13 06:40", None, None, 0, at="eod"),               # 잔고 실패 (b)
    _snap("2026-10-14 06:40", None, None, 0, at="eod"),
    _snap("2026-10-15 06:40", None, None, 0, at="eod"),
]


@pytest.fixture
def ledger_store(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_DB", str(tmp_path / "stock_report.db"))
    monkeypatch.setenv("KOREA_MOCK_SEED", "100000")
    import store
    for row in _LEDGER:
        store.append(COL, row)
    return store


def _us_glance():
    from dashboard import views
    return next(r for r in views.paper_glance() if r["surface"] == "us_mock")


def test_a_exec_fix2_ops_ledger_shows_fake_reset_loss_before_repair(ledger_store):
    """복구 전 원장 그대로면 레일이 시드 폴백 $100,000 과 -60% 를 보여준다 (재현 확인)."""
    row = _us_glance()
    assert row["nav"] == 100_000.0
    assert row["current_cum_ret"] < -55.0


def test_a_exec_fix2_repair_dry_run_writes_nothing(ledger_store, tmp_path, capsys):
    import repair_mock_generation_boundary as repair

    before = ledger_store.all(COL)
    assert repair.main(["--collection", COL, "--backup-dir", str(tmp_path / "bk")]) == 0

    out = capsys.readouterr().out
    assert "[DRY-RUN]" in out and "세대 경계 삽입" in out and "262,224.57" in out
    assert ledger_store.all(COL) == before
    assert ledger_store.all(f"{COL}_generations") == []
    assert not (tmp_path / "bk").exists()


def test_a_exec_fix2_repair_apply_requires_confirm(ledger_store, tmp_path):
    import repair_mock_generation_boundary as repair

    before = ledger_store.all(COL)
    assert repair.main(["--collection", COL, "--apply", "--backup-dir", str(tmp_path / "bk")]) == 2
    assert ledger_store.all(COL) == before
    assert not (tmp_path / "bk").exists()


def test_a_exec_fix2_repair_apply_inserts_boundary_marks_invalid_and_is_idempotent(ledger_store, tmp_path):
    import repair_mock_generation_boundary as repair
    from lib import mock_generations

    backup_dir = tmp_path / "bk"
    assert repair.main(["--collection", COL, "--apply", "--confirm", "--backup-dir", str(backup_dir)]) == 0

    rows = ledger_store.all(COL)
    assert len(rows) == len(_LEDGER) + 1                      # 행 삭제 없음, 경계 1건 삽입
    boundary_at = next(i for i, r in enumerate(rows) if r["kind"] == "generation_boundary")
    assert rows[boundary_at + 1]["date"] == "2026-10-13 00:03"
    boundary = rows[boundary_at]
    assert boundary["final_nav"] == 262_224.57 and boundary["generation"] == 1
    assert boundary["close_nav_date"] == "2026-10-10 06:40"
    invalid = {r["date"]: r["invalid"] for r in rows if r.get("invalid")}
    assert invalid == {
        "2026-08-25 00:03": "incomplete_balance", "2026-08-26 00:04": "incomplete_balance",
        "2026-10-13 00:03": "unobserved_nav", "2026-10-13 06:40": "nav_missing",
        "2026-10-14 06:40": "nav_missing", "2026-10-15 06:40": "nav_missing",
    }
    archive = ledger_store.all(f"{COL}_generations")
    assert len(archive) == 1 and archive[0]["final_nav"] == 262_224.57
    assert archive[0]["mdd_pct"] == 0.0                         # 반쪽 잔고 낙폭이 MDD 에 없음

    # 백업: SQLite backup API 사본 + manifest, 원본 행 수 그대로
    (stamp_dir,) = list(backup_dir.iterdir())
    assert (stamp_dir / "manifest.json").exists()
    con = sqlite3.connect(str(stamp_dir / "stock_report.db"))
    try:
        n = con.execute("SELECT COUNT(*) FROM collections WHERE name=?", (COL,)).fetchone()[0]
    finally:
        con.close()
    assert n == len(_LEDGER)

    # 대시보드 레일: 시드 폴백 $100,000·-60% 대신 직전 세대 마감 NAV + 새 세대 대기 + 잔고 실패 표시
    row = _us_glance()
    assert row["awaiting_generation"] is True
    assert row["nav"] == 262_224.57
    assert round(row["cum_ret"], 4) == round((262_224.57 / 250_000.0 - 1) * 100, 4)
    assert row["balance_failure"]["date"] == "2026-10-15 06:40"

    # 멱등: 두 번째 적용은 변경·백업 없음
    assert repair.main(["--collection", COL, "--apply", "--confirm", "--backup-dir", str(backup_dir)]) == 0
    assert ledger_store.all(COL) == rows
    assert len(ledger_store.all(f"{COL}_generations")) == 1
    assert len(list(backup_dir.iterdir())) == 1

    # 계좌 재신청 후 첫 실제 잔고(시드)는 새 세대의 첫 기록 — 추가 리셋 경계를 만들지 않는다
    res = mock_generations.record_snapshot(COL, nav=100_000.0, cash=100_000.0, positions={},
                                           seed=100_000.0, now="2026-10-20 06:40")
    assert res["recorded"] is True and res["reset"] is None
    row = _us_glance()
    assert row["awaiting_generation"] is False and row["generation"] == 2
    assert row["nav"] == 100_000.0 and row["current_cum_ret"] == 0.0


def test_a_exec_fix2_repair_resumes_missing_archive_only(ledger_store, tmp_path):
    """경계는 쓰였는데 아카이브 쓰기 전에 중단된 상태 → 재실행이 아카이브만 보충."""
    import repair_mock_generation_boundary as repair

    rows = ledger_store.all(COL)
    plan = repair.plan_repair(rows, [], collection=COL, seed=100_000.0)
    ledger_store.replace_all(COL, repair.apply_plan_to_rows(rows, plan))   # 아카이브 append 전 중단

    result = repair.apply_repair(COL, seed=100_000.0, backup_dir=tmp_path / "bk", store_module=ledger_store)

    assert result["applied"] is True
    assert result["plan"]["marks"] == [] and result["plan"]["boundary"] is None
    assert [a["generation"] for a in ledger_store.all(f"{COL}_generations")] == [1]
    assert len(ledger_store.all(COL)) == len(_LEDGER) + 1
