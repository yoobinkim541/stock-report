"""portfolio_sync_server._update_portfolio — store 그림자는 파일 락 보유 중에 기록돼야
늦게 끝난 writer 가 낡은 snapshot 으로 그림자를 덮지 않는다 (파일 권위 ↔ store 정합)."""
import json
from contextlib import contextmanager

import portfolio_sync_server as pss


def test_shadow_written_while_file_lock_held(monkeypatch, tmp_path):
    path = tmp_path / "portfolio_snapshot.json"
    path.write_text(json.dumps({"overseas_general": {"holdings_usd": []}}), encoding="utf-8")
    monkeypatch.setattr(pss, "PORTFOLIO_PATH", str(path))

    events: list[str] = []
    import safe_io

    @contextmanager
    def _lock(_p):
        events.append("lock")
        yield
        events.append("unlock")
    monkeypatch.setattr(safe_io, "file_write_lock", _lock)
    monkeypatch.setattr(pss, "_shadow_to_store", lambda snap: events.append("shadow"))
    monkeypatch.setattr(pss, "_apply_portfolio_update", lambda snap, data: "ok")

    assert pss._update_portfolio({}) == "ok"
    assert events == ["lock", "shadow", "unlock"]
