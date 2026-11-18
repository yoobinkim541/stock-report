"""ml/adaptive/ledger — 교차 프로세스 멱등성(확인→append 락)."""

def test_log_decision_idempotent_under_concurrent_writers(tmp_path):
    """여러 스레드(프로세스 대역)가 같은 id 를 동시에 기록해도 원장에는 1줄만 남는다."""
    import threading
    from ml.adaptive.ledger import Ledger

    led = Ledger("t", base_dir=tmp_path)
    barrier = threading.Barrier(8)

    def w():
        barrier.wait()
        led.log_decision({"date": "2026-01-01", "ticker": "AAPL", "x": 1})
        led.log_outcome({"decision_id": "2026-01-01:AAPL", "r": 0.1})

    ts = [threading.Thread(target=w) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(led.read_decisions()) == 1
    assert len(led.read_outcomes()) == 1
