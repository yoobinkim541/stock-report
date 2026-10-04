import json

from crons.recommendation_index_refresh import main
from ml.recommendation_engine import build_snapshot, rank_snapshots
from ml.recommendation_features import build_news_feature_frame
from reports.recommendation_index import RecommendationIndex


def test_raw_event_to_features_to_shadow_snapshot(tmp_path):
    raw = tmp_path / 'events-test.jsonl'
    raw.write_text(json.dumps({
        'source': 'saveticker', 'tickers': ['AAPL'], 'title': 'Earnings',
        'url': 'https://example.test/earnings', 'body': 'Earnings improved',
        'collected_at': '2026-09-27T10:00:00+00:00',
    }) + '\n')
    db = tmp_path / 'index.db'
    assert main(['--db-path', str(db), '--cache-dir', str(tmp_path)]) == 0
    index = RecommendationIndex(db, tmp_path)
    frame = build_news_feature_frame('AAPL', '2026-09-27', as_of='2026-09-27T12:00:00+00:00', index=index, labels=[])
    assert frame.iloc[0]['article_count'] == 1
    snapshot = build_snapshot('entry', {'ticker': 'AAPL', 'score': 0.8, 'horizon': 20},
                              source_features=frame.iloc[0].to_dict())
    ranked = rank_snapshots([snapshot])
    assert ranked[0]['horizon'] == '20d'
    assert ranked[0]['selection_status'] == 'shadow'
    assert ranked[0]['news_contribution'] == 0
    assert ranked[0]['freshness_seconds'] == 7200
