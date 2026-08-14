"""tests/test_notion_holdings_sync.py — _sync_holdings_db 재매수 시 중복 행 회귀.

Notion 데이터베이스 query API 는 기본적으로 archived 페이지를 반환하지 않는다.
매도 시 보유종목 행을 archive 하면, 이후 같은 티커를 재매수했을 때 query 결과의
`existing` 맵에 그 티커가 더 이상 없어 신규 페이지를 create 해버려 archived 된
구 행과 신규 행이 함께 남는(중복) 버그를 재현·검증한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crons.notion_sync as ns


class _FakeResponse:
    def __init__(self, ok=True, json_data=None):
        self.ok = ok
        self._json = json_data or {}
        self.status_code = 200 if ok else 400
        self.text = ""

    def json(self):
        return self._json


def _run_sync(monkeypatch, tmp_path, rows, existing_pages, calls):
    """existing_pages: 이번 query 응답이 반환할 {ticker: page_id} (archived 미포함).
    calls: post/patch 호출 기록 리스트."""
    monkeypatch.setattr(ns, "_load_holdings", lambda: rows)
    monkeypatch.setattr(ns, "_ensure_holdings_db", lambda parent: "db-1")
    monkeypatch.setattr(ns, "_HOLDINGS_PAGE_CACHE", str(tmp_path / "holdings_pages.json"))

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(("post", url, json))
        if url.endswith("/query"):
            results = [
                {"id": pid, "properties": {"Ticker": {"title": [{"plain_text": tk}]}}}
                for tk, pid in existing_pages.items()
            ]
            return _FakeResponse(True, {"results": results, "has_more": False})
        if url.endswith("/pages"):
            return _FakeResponse(True, {"id": f"new-{len(calls)}"})
        return _FakeResponse(False)

    def fake_patch(url, headers=None, json=None, timeout=None):
        calls.append(("patch", url, json))
        return _FakeResponse(True)

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.patch", fake_patch)
    ns._sync_holdings_db("parent-1")


def test_rebuy_after_sell_reuses_archived_page_instead_of_duplicating(monkeypatch, tmp_path):
    row = {"ticker": "AAPL", "name": "Apple", "ccy": "USD", "shares": 10,
           "avg": 150.0, "cur": 160.0, "value": 1600.0, "ret": 0.05, "weight": 1.0}
    other = {"ticker": "MSFT", "name": "Microsoft", "ccy": "USD", "shares": 5,
              "avg": 300.0, "cur": 310.0, "value": 1550.0, "ret": 0.03, "weight": 1.0}

    # 1) 최초 매수 — 신규 생성
    calls1: list = []
    _run_sync(monkeypatch, tmp_path, [row, other], existing_pages={}, calls=calls1)
    creates = [c for c in calls1 if c[0] == "post" and c[1].endswith("/pages")]
    assert len(creates) == 2
    page_id = "new-2"  # fake_post 의 len(calls) 기반 id (post query=call#1, post AAPL create=call#2)

    # 2) AAPL 매도, MSFT 는 계속 보유 — AAPL 만 archive
    calls2: list = []
    _run_sync(monkeypatch, tmp_path, [other], existing_pages={"AAPL": page_id, "MSFT": "msft-1"}, calls=calls2)
    archives = [c for c in calls2 if c[0] == "patch" and c[2] == {"archived": True}]
    assert len(archives) == 1
    assert archives[0][1].endswith(f"/pages/{page_id}")

    # 3) 재매수 — archived 페이지는 query 에 더 이상 안 나옴(existing_pages 에서 AAPL 빠짐)
    calls3: list = []
    _run_sync(monkeypatch, tmp_path, [row, other], existing_pages={"MSFT": "msft-1"}, calls=calls3)
    creates3 = [c for c in calls3 if c[0] == "post" and c[1].endswith("/pages")]
    aapl_patch = [c for c in calls3 if c[0] == "patch" and c[1].endswith(f"/pages/{page_id}")]

    assert creates3 == [], "재매수 시 신규 페이지를 또 만들면 안 됨(중복 행)"
    assert len(aapl_patch) == 1
    assert aapl_patch[0][2].get("archived") is False


def test_ensure_holdings_db_writes_cache_atomically(monkeypatch, tmp_path):
    """감사 #28 — DB id 캐시가 open(...,'w')+json.dump 직접 쓰기라, 프로세스가
    쓰기 도중 죽으면 캐시 파일이 잘린/손상된 채 남을 수 있었음.
    safe_io.atomic_write_json 경유(임시파일→os.replace)로 써야 한다."""
    import json
    import safe_io

    cache_path = tmp_path / "notion_holdings_db.json"
    monkeypatch.setattr(ns, "HOLDINGS_DB_CACHE", str(cache_path))

    atomic_calls = []
    orig_atomic_write = safe_io.atomic_write_json

    def spy_atomic_write(path, obj, **kwargs):
        atomic_calls.append((path, obj))
        return orig_atomic_write(path, obj, **kwargs)

    monkeypatch.setattr(safe_io, "atomic_write_json", spy_atomic_write)

    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(False)  # 캐시 미스 → 신규 생성 경로

    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(True, {"id": "db-new-1"})

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr("requests.post", fake_post)

    did = ns._ensure_holdings_db("parent-1")

    assert did == "db-new-1"
    assert len(atomic_calls) == 1, "raw open(w)+json.dump 를 계속 쓰면 spy 가 호출 안 됨"
    assert atomic_calls[0][0] == str(cache_path)
    assert json.loads(cache_path.read_text(encoding="utf-8"))["database_id"] == "db-new-1"
