import importlib
from datetime import datetime, timezone


def test_private_runtime_archives_source_without_production_queue(tmp_path,monkeypatch):
    module=importlib.import_module('reports.saveticker_runtime')
    monkeypatch.setenv('HERMES_SOURCE_WEBHOOK_SECRET','local-test-secret')
    root=tmp_path/'private-sources'
    html='<article><h1>허용 원문</h1><div data-article-body><p>끝까지 보존</p></div></article>'
    url='https://saveticker.com/news/123'
    class Browser:
        def read(self,value): return {'url':url,'html':html}
    result=module.collect_private(root=root,limit=1,article_urls=[url],browser=Browser())
    assert result['delivered']==1
    assert len(list((root/'raw').rglob('*.html')))==1
    assert len(list((root/'cache').glob('events-*.jsonl')))==1
    assert not (tmp_path/'source-cache').exists()
    assert not (root/'wiki').exists()


def test_private_runtime_rejects_production_storage_root(monkeypatch):
    from pathlib import Path
    module=importlib.import_module('reports.saveticker_runtime')
    import pytest
    with pytest.raises(ValueError):
        module.collect_private(root=Path.home()/'reports',browser=object())
