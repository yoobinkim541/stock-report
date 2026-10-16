from copy import deepcopy

from dashboard import wiki_browser


def sample_knowledge():
    return {
        "claims": [{
            "claim_id": "claim-1",
            "text": "Capacity increased; realized demand remains unproven.",
            "as_of": "2026-10-09",
            "scope": "Capacity, not orders",
            "knowledge_state": "draft",
            "source_revision_id": "revision-7",
        }],
        "limitations": ["OCR must be checked against the original source."],
        "relations": [{
            "type": "compete",
            "from_as_of": "2026-10-08",
            "to_as_of": "2026-10-10",
            "scope_explanation": "Two reports describe different capacity windows.",
        }],
        "integrations": [{"revision_id": "revision-8", "role": "qualifier", "text": "Does not establish customer orders."}],
        "private_notes": ["Do not display this."],
        "arbitrary_metadata": {"token": "secret"},
    }


def test_wiki_page_object_preserves_structured_evidence_without_aliasing():
    source = sample_knowledge()
    page = wiki_browser.WikiPage(
        id="object-page", title="Capacity", source_knowledge=source,
    )
    model = wiki_browser.build_selected_evidence_model(page)
    assert model["source_knowledge"]["claims"] == source["claims"]
    assert model["source_knowledge"]["limitations"] == source["limitations"]
    assert "private_notes" not in model["source_knowledge"]
    exported = page.as_dict()
    exported["source_knowledge"]["claims"][0]["text"] = "mutated"
    assert source["claims"][0]["text"] != "mutated"


def test_selected_evidence_exposes_only_public_structured_schema_as_deep_copy():
    source = sample_knowledge()
    original = deepcopy(source)
    model = wiki_browser.build_selected_evidence_model({
        "id": "structured-only", "title": "Structured record", "status": "draft",
        "source_refs": [], "source_knowledge": source,
    })

    assert model["source_knowledge"] == {key: original[key] for key in (
        "claims", "limitations", "relations", "integrations"
    )}
    assert model["source_knowledge"] is not source
    assert model["source_knowledge"]["claims"] is not source["claims"]
    model["source_knowledge"]["claims"][0]["text"] = "changed"
    assert source == original
    assert "private_notes" not in str(model)
    assert "arbitrary_metadata" not in str(model)


class FakeRenderer:
    def __init__(self):
        self.calls = []

    def expander(self, label, **kwargs):
        self.calls.append(("expander", label))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def markdown(self, value):
        self.calls.append(("markdown", str(value)))

    def caption(self, value):
        self.calls.append(("caption", str(value)))

    def info(self, value):
        self.calls.append(("info", str(value)))


def test_read_view_renders_structured_only_evidence_with_provenance_and_state():
    page = {
        "id": "structured-only", "title": "Capacity update", "status": "draft",
        "source_refs": [], "source_knowledge": sample_knowledge(),
    }
    model = wiki_browser.build_selected_evidence_model(page)
    renderer = FakeRenderer()

    wiki_browser._render_selected_evidence(model, [], st_module=renderer)

    rendered = "\n".join(value for _kind, value in renderer.calls)
    assert "Capacity increased; realized demand remains unproven." in rendered
    assert "2026-10-09" in rendered
    assert "Capacity, not orders" in rendered
    assert "draft" in rendered
    assert "revision-7" in rendered
    assert "OCR must be checked" in rendered
    assert "2026-10-08" in rendered and "2026-10-10" in rendered
    assert "Two reports describe different capacity windows." in rendered
    assert "qualifier" in rendered
    assert "revision-8" in rendered
    assert "Do not display this." not in rendered
    assert "secret" not in rendered
    assert "본문이 아직 없습니다" not in rendered
    assert not any(kind == "info" for kind, _value in renderer.calls)


def test_read_view_exposes_unresolved_conflict_disposition():
    renderer = FakeRenderer()
    wiki_browser._render_selected_evidence({'source_knowledge': {'integrations': [{
        'revision_id': 'a' * 64, 'role': 'conflict', 'relation_state': 'unresolved',
        'relation_note': 'Conflict is not adjudicated.'}]}}, [], st_module=renderer)
    rendered = '\n'.join(value for _, value in renderer.calls)
    assert 'unresolved' in rendered
    assert 'not adjudicated' in rendered


def test_normalization_preserves_public_structured_knowledge_without_mutating_input():
    source = sample_knowledge()
    original = deepcopy(source)
    page = wiki_browser._normalize_page({
        "title": "Structured", "source_refs": [], "source_knowledge": source,
    })

    assert page["source_knowledge"] == {key: original[key] for key in (
        "claims", "limitations", "relations", "integrations"
    )}
    assert page["source_knowledge"] is not source
    assert source == original
