from agent_console import wiki_contract


def test_maintenance_schema_covers_resumable_compilation_and_knowledge_protection():
    schema = wiki_contract.maintenance_schema()
    assert 'source_span' in schema
    assert '20,000' in schema
    assert 'all spans' in schema
    assert 'source_knowledge' in schema
    assert 'Source Knowledge' in schema
    assert 'four new chunk calls' in schema
    assert '1,600' in schema
