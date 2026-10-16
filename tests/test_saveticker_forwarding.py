"""Synthetic, offline contracts for the private-source alert path."""
from docs.audits.saveticker_forwarding_contract import *


def test_partial_delivery_resumes_without_repeating_acknowledged_part(monkeypatch, tmp_path):
    row = event()
    row['body_raw'] = 'synthetic body\n' * 900
    calls = []
    setup_sender(monkeypatch, tmp_path, lambda text: (calls.append(text), len(calls) != 2)[1])
    monkeypatch.setattr(N, 'fetch_breaking_news', lambda now: [row])
    N.main()
    N.main()
    assert len(calls) > 2
    assert calls[1] == calls[2]
    assert calls.count(calls[0]) == 1
    assert row['id'] in N._load_state()['sent_ids']


def test_header_breaking_label_is_preserved_without_snapshot_category():
    from reports.saveticker_hermes_collector import extract_article
    raw = '<article><header><span>속보</span><h1>Test</h1></header><div data-article-body>Body</div></article>'
    assert extract_article({'url': URL, 'html': raw})['category'] == '속보'


def test_unknown_publication_is_not_replaced_by_collection_time(monkeypatch):
    monkeypatch.setattr(N, '_realtime_tag', lambda tags: '')
    row = event()
    row['published_at'] = ''
    row['source_collected_at'] = datetime.now(timezone.utc).isoformat()
    assert '발행 시각 미확인' in N._format_alert(row, 8, 'test')


def test_chunks_preserve_every_character_including_blank_lines():
    message = ('A\n\n\n' * 2000) + '😀' * 4000
    parts = N._alert_parts(message)
    assert ''.join(parts) == message
    assert all(len(part.encode('utf-16-le')) // 2 <= 4096 for part in parts)


def test_private_source_does_not_use_external_llm(monkeypatch, tmp_path):
    allowed = []
    setup_sender(monkeypatch, tmp_path, lambda text: True)
    def judge(row, *, allow_llm):
        allowed.append(allow_llm)
        return 8, 'test', False
    monkeypatch.setattr(N, 'judge_importance', judge)
    N.main()
    assert allowed == [False]
