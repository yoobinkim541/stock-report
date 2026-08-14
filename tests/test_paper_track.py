from __future__ import annotations


def test_main_skips_without_error_when_previous_run_still_holds_lock(monkeypatch, tmp_path):
    """감사 #29 — 겹쳐 도는 두 실행이 같은 track 을 각자 read-modify-write 하면
    늦게 쓴 쪽이 앞선 실행의 기록을 덮어써 유실될 수 있었다(락 없음)."""
    import safe_io
    from crons import paper_track as cron

    lock_target = str(tmp_path / "paper_track")
    monkeypatch.setattr(cron, "_LOCK_TARGET", lock_target)

    def _boom(*a, **k):
        raise AssertionError("_run 이 호출되면 안 됨 — 이전 실행이 아직 락을 쥐고 있어야 함")

    monkeypatch.setattr(cron, "_run", _boom)

    with safe_io.file_write_lock(lock_target, timeout=1):
        rc = cron.main()               # 겹치는 실행을 시뮬레이션 — 조용히 스킵돼야 함

    assert rc == 0


def test_main_runs_normally_when_no_lock_held(monkeypatch, tmp_path):
    from crons import paper_track as cron

    lock_target = str(tmp_path / "paper_track")
    monkeypatch.setattr(cron, "_LOCK_TARGET", lock_target)

    calls = []
    monkeypatch.setattr(cron, "_run", lambda: calls.append(1) or 0)

    rc = cron.main()

    assert rc == 0
    assert calls == [1]
