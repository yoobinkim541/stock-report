"""store.update_all — read-modify-write 가 단일 쓰기 트랜잭션이라 동시 writer 의 변경이 보존된다.
(종전 all()→replace_all() 분리 호출은 봇·크론이 겹치면 한쪽 변경이 사라졌다.)"""
import threading

import pytest

import store


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_DB", str(tmp_path / "t.db"))
    store._initialized.clear()
    yield
    store._initialized.clear()


def test_update_all_preserves_concurrent_appends(db):
    store.replace_all("c", [])
    n_threads, per = 8, 25
    barrier = threading.Barrier(n_threads)

    def worker(k):
        barrier.wait()
        for i in range(per):
            store.update_all("c", lambda items, k=k, i=i: items + [{"k": k, "i": i}])

    ts = [threading.Thread(target=worker, args=(k,)) for k in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    items = store.all("c")
    assert len(items) == n_threads * per
    assert len({(it["k"], it["i"]) for it in items}) == n_threads * per


def test_append_serializes_seq_under_contention(db):
    store.replace_all("a", [])
    barrier = threading.Barrier(6)
    errors = []

    def worker():
        barrier.wait()
        try:
            for i in range(20):
                store.append("a", {"i": i})
        except Exception as e:  # IntegrityError 였던 경쟁
            errors.append(e)

    ts = [threading.Thread(target=worker) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    assert store.count("a") == 120


def test_tax_tracker_add_and_delete_roundtrip(db, monkeypatch):
    import tax_tracker as tt
    monkeypatch.setattr(tt, "TAX_FILE", None)
    monkeypatch.setattr(store, "load_collection", lambda name, path, **k: store.all(name))
    tt.add_sell("AAPL", 1, 100.0, 110.0, 1400.0)
    tt.add_sell("MSFT", 2, 200.0, 190.0, 1400.0)
    assert [r["ticker"] for r in tt.get_all_records()] == ["AAPL", "MSFT"]
    assert tt.delete_record(5) is None
    assert tt.delete_record(1)["ticker"] == "AAPL"
    assert [r["ticker"] for r in tt.get_all_records()] == ["MSFT"]
