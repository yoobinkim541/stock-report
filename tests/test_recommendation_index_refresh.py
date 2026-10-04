import fcntl
import json
import sqlite3
from datetime import datetime, timezone

from reports.recommendation_index import RecommendationIndex


def seed(path, count=3):
    path.write_text(''.join(json.dumps({
        "source": "saveticker", "tickers": ["AAPL"],
        "url": f"https://example.test/{i}", "title": str(i),
        "collected_at": "2026-09-27T10:00:00+00:00",
    }) + '\n' for i in range(count)))


def test_refresh_is_bounded_resumable_and_does_not_rewrite_raw(tmp_path, capsys):
    from crons.recommendation_index_refresh import main
    raw = tmp_path / 'events-test.jsonl'
    seed(raw)
    original = raw.read_bytes()
    args = ['--cache-dir', str(tmp_path), '--db-path', str(tmp_path / 'index.db'), '--max-rows', '2']
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first['fetched'] == first['indexed'] == 2
    assert first['pending_bytes'] > 0
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['indexed'] == 1
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['indexed'] == 0
    assert raw.read_bytes() == original


def test_byte_limit_and_noop_do_not_rebuild_features(tmp_path, monkeypatch):
    raw = tmp_path / 'events-test.jsonl'
    seed(raw)
    size = len(raw.read_bytes().splitlines(keepends=True)[0])
    index = RecommendationIndex(tmp_path / 'index.db', tmp_path)
    first = index.ingest(max_bytes=size, max_rows=100)
    assert first['indexed'] == 1
    assert first['bytes_read'] <= size
    assert index.ingest(max_bytes=size * 3)['indexed'] == 2
    monkeypatch.setattr(index, '_rebuild_features', lambda *_: (_ for _ in ()).throw(AssertionError('full rebuild')))
    assert index.ingest()['indexed'] == 0
    assert index.ingest()['bytes_read'] == 0


def test_lock_contention_is_a_successful_skip(tmp_path, capsys):
    from crons.recommendation_index_refresh import main
    db = tmp_path / 'index.db'
    with (tmp_path / 'index.db.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main(['--db-path', str(db), '--cache-dir', str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'locked'
    assert not db.exists()


def test_corrupt_database_is_nonzero_but_health_survives(tmp_path, capsys):
    from crons.recommendation_index_refresh import main
    from reports.recommendation_health import collect, format_report
    db = tmp_path / 'index.db'
    db.write_bytes(b'not sqlite' * 100)
    assert main(['--db-path', str(db), '--cache-dir', str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'error'
    health = collect(index=RecommendationIndex(db, tmp_path), outcomes=[], model=None)
    assert health['index']['status'] == 'unavailable'
    assert 'unavailable' in format_report(health)


def test_health_counts_actual_fallbacks_duplicates_nonfinite_and_freshness(tmp_path):
    from reports.recommendation_health import collect, format_report
    index = RecommendationIndex(tmp_path / 'index.db', tmp_path)
    raw = tmp_path / 'events-test.jsonl'
    seed(raw, 1)
    raw.write_text(raw.read_text().replace('saveticker', 'unknown_source'))
    index.ingest()
    assert index.status()['fallback_count'] == 0
    index.record_fallback('tail_query')
    health = collect(index=index, outcomes=[
        {'decision_id': 'a:h20', 'horizon': 20, 'fwd_ret': float('nan')},
        {'decision_id': 'a:h20d', 'horizon': '20d', 'fwd_excess': float('inf')},
    ], model={'train_end_date': '2020-01-01', 'trained_at': '2020-01-02'},
        source_health={'saveticker': {'availability': 'blocked'}},
        now=datetime(2026, 9, 27, tzinfo=timezone.utc))
    assert health['fallback_count'] == 1
    assert health['canonical_duplicate_count'] == 1
    assert health['invalid_metric_count'] == 2
    assert health['model']['stale'] is True
    assert health['blocked_sources'] == ['saveticker']
    assert health['index']['last_offset'] > 0
    assert 'blocked' in format_report(health)


def test_partial_tail_and_oversize_record_are_not_consumed(tmp_path):
    raw = tmp_path / 'events-test.jsonl'
    seed(raw, 1)
    index = RecommendationIndex(tmp_path / 'index.db', tmp_path)
    result = index.ingest(max_bytes=10)
    assert result['indexed'] == 0
    assert result['bytes_read'] == 10
    assert result['budget_blocked'] is True
    assert index.ingest(max_bytes=10000)['indexed'] == 1


def test_incremental_refresh_only_updates_affected_days(tmp_path):
    raw = tmp_path / 'events-test.jsonl'
    seed(raw, 1)
    index = RecommendationIndex(tmp_path / 'index.db', tmp_path)
    index.ingest()
    with sqlite3.connect(index.db_path) as connection:
        connection.execute("CREATE TRIGGER prevent_unrelated_delete BEFORE DELETE ON ticker_day_features WHEN OLD.ticker = 'AAPL' BEGIN SELECT RAISE(ABORT, 'unrelated day'); END")
    raw2 = tmp_path / 'events-second.jsonl'
    seed(raw2, 1)
    raw2.write_text(raw2.read_text().replace('AAPL', 'MSFT'))
    assert index.ingest()['indexed'] == 1
