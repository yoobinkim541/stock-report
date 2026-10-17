"""Unattended distillation cannot fall back to obsolete template generation."""
import pytest


@pytest.mark.parametrize('enabled', ['0', '1'])
def test_legacy_entrypoint_uses_only_bounded_compile_worker(monkeypatch, enabled):
    from reports import wiki_distillation
    from scripts import run_public_wiki_rollout
    monkeypatch.setenv('WIKI_SOURCE_PIPELINE_ENABLED', enabled)
    monkeypatch.setattr('sys.argv', ['distillation', '--limit', '3'])
    monkeypatch.setattr(wiki_distillation, 'run', lambda **kw: pytest.fail('legacy generator ran'))
    called = []
    monkeypatch.setattr(run_public_wiki_rollout, 'main', lambda argv: called.append(argv) or 0)
    assert wiki_distillation.main() == 0
    assert called == [['--source-limit', '3', *(['--apply'] if enabled == '1' else [])]]
