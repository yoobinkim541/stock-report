"""
test_mock_generations.py — 모의계좌 만기 리셋 시 성과 세대 분리 테스트.

검증:
  - 경계 없을 때 active_snapshots() 는 전체 히스토리 반환
  - close_generation() 이 아카이브 레코드 + 경계 마커를 남김
  - 경계 이후 active_snapshots() 는 새 세대 스냅샷만 반환 (구 세대 인셉션에
    오염되지 않음 — 계좌 리셋이 전략 손실로 오인되는 왜곡 방지)
  - 잔고 조회 실패 시 close_generation() 은 예외 (구 계좌가 죽은 상태에서 마감 금지)
  - generation_count() 는 마감 횟수 + 1
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from lib import mock_generations  # noqa: E402


class _FakeStore:
    def __init__(self):
        self._data: dict[str, list[dict]] = {}

    def all(self, name):
        return list(self._data.get(name, []))

    def append(self, name, item):
        self._data.setdefault(name, []).append(dict(item))
        return len(self._data[name])


def _snap(date, nav):
    return {"date": date, "kind": "snapshot", "nav": nav}


def test_active_snapshots_returns_all_when_no_boundary():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 10_000_000))
    store.append("kr_mock_history", _snap("2026-01-06 06:40", 10_200_000))

    snaps = mock_generations.active_snapshots("kr_mock_history", store_module=store)

    assert len(snaps) == 2
    assert snaps[0]["nav"] == 10_000_000


def test_close_generation_archives_summary_and_marks_boundary():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 10_000_000))
    store.append("kr_mock_history", _snap("2026-04-03 06:40", 11_500_000))

    def get_balance():
        return {
            "ok": True,
            "nav": 11_800_000,
            "pos_value": 10_000_000,
            "cash_krw": 1_800_000,
            "positions": {
                "005930": {"name": "삼성전자", "shares": 10, "value": 10_000_000},
                "000660": {"name": "SK하이닉스", "shares": 0, "value": 0},  # 청산분 제외돼야 함
            },
        }

    summary = mock_generations.close_generation(
        "kr_mock_history",
        get_balance_fn=get_balance,
        reason="3개월 만기 갱신",
        max_drawdown_fn=lambda series: 0.05,
        store_module=store,
    )

    assert summary["generation"] == 1
    assert summary["inception_nav"] == 10_000_000
    assert summary["final_nav"] == 11_800_000
    assert round(summary["cum_return_pct"], 2) == 18.0
    assert summary["mdd_pct"] == 5.0
    assert summary["holdings_at_close"] == [
        {"code": "005930", "name": "삼성전자", "shares": 10, "value": 10_000_000}
    ]

    archived = store.all("kr_mock_history_generations")
    assert len(archived) == 1
    assert archived[0]["reason"] == "3개월 만기 갱신"

    boundaries = [r for r in store.all("kr_mock_history") if r.get("kind") == "generation_boundary"]
    assert len(boundaries) == 1
    assert boundaries[0]["generation"] == 1


def test_active_snapshots_excludes_prior_generation_after_close():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 10_000_000))
    store.append("kr_mock_history", _snap("2026-04-03 06:40", 6_000_000))  # 리셋 직전 큰 낙폭처럼 보일 스냅샷

    mock_generations.close_generation(
        "kr_mock_history",
        get_balance_fn=lambda: {"ok": True, "nav": 6_000_000, "positions": {}},
        max_drawdown_fn=lambda series: 0.4,
        store_module=store,
        now="2026-04-05 09:00",
    )

    # 새 세대: 리셋된 새 계좌가 시드 자본으로 시작
    store.append("kr_mock_history", _snap("2026-04-06 06:40", 10_000_000))

    snaps = mock_generations.active_snapshots("kr_mock_history", store_module=store)

    assert len(snaps) == 1
    assert snaps[0]["nav"] == 10_000_000  # 구 세대(10M→6M 낙폭)가 새 계산에 섞이지 않음


def test_close_generation_rejects_when_balance_fetch_fails():
    store = _FakeStore()
    store.append("kr_mock_history", _snap("2026-01-05 06:40", 10_000_000))

    try:
        mock_generations.close_generation(
            "kr_mock_history",
            get_balance_fn=lambda: {"ok": False},
            store_module=store,
        )
        assert False, "잔고 조회 실패 시 예외가 발생해야 한다"
    except RuntimeError as e:
        assert "잔고 조회 실패" in str(e)

    assert store.all("kr_mock_history_generations") == []


def test_generation_count_tracks_closed_boundaries():
    store = _FakeStore()
    assert mock_generations.generation_count("kr_mock_history", store_module=store) == 1

    store.append("kr_mock_history", {"date": "2026-04-03 06:40", "kind": "generation_boundary", "generation": 1})
    assert mock_generations.generation_count("kr_mock_history", store_module=store) == 2
