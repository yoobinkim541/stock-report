import json
import time
from datetime import datetime, timezone
from pathlib import Path

from reports.recommendation_index import RecommendationIndex


def test_10k_ingest_and_warm_100_candidate_queries(tmp_path, monkeypatch):
    raw = tmp_path / 'events-performance.jsonl'
    raw.write_text(''.join(json.dumps({
        'source': 'saveticker', 'tickers': [f'T{i % 100:03}'],
        'url': f'https://example.test/{i}', 'title': str(i),
        'collected_at': '2026-09-27T10:00:00+00:00',
    }) + '\n' for i in range(10000)))
    index = RecommendationIndex(tmp_path / 'index.db', tmp_path)
    start = time.perf_counter()
    result = index.ingest(max_rows=10000, max_bytes=10_000_000)
    elapsed = time.perf_counter() - start
    assert result['indexed'] == 10000
    assert index.status()['last_ingest_seconds'] >= 0
    scans = []
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path.name.startswith('events-'):
            scans.append(str(path))
            raise AssertionError('raw scan during warm query')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded_open)
    tickers = [f'T{i:03}' for i in range(100)]
    asof = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    index.ticker_day_features_batch(tickers, asof=asof)
    durations = []
    for _ in range(20):
        start = time.perf_counter()
        rows = index.ticker_day_features_batch(tickers, asof=asof)
        durations.append((time.perf_counter() - start) * 1000)
        assert len(rows) == 100
        assert all(rows[t][0]['event_count'] == 100 for t in tickers)
    p95 = sorted(durations)[18]
    print(f'10k_ingest_seconds={elapsed:.4f} warm100_p95_ms={p95:.3f} raw_scans={len(scans)}')
    assert p95 <= 300
    assert scans == []
