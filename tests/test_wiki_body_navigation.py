import inspect

from dashboard import wiki_browser


def test_body_transform_resolves_only_known_ids_and_unique_legacy_titles():
    pages = [
        {"id": "known-id", "title": "Known Page"},
        {"id": "legacy-id", "title": "Legacy Page"},
        {"id": "duplicate-a", "title": "Repeated"},
        {"id": "duplicate-b", "title": "Repeated"},
    ]
    body = "[[known-id|Readable]] [[Legacy Page]] [[missing-id|Missing]] [[Repeated]] https://example.com"

    markdown, references = wiki_browser.transform_wiki_body(body, pages)

    assert "Readable" in markdown
    assert "[[missing-id|Missing]]" in markdown
    assert "[[Repeated]]" in markdown
    assert "https://example.com" in markdown
    assert [(page["id"], label) for page, label in references] == [
        ("known-id", "Readable"), ("legacy-id", "Legacy Page")
    ]
    assert body.startswith("[[known-id|Readable]]")


def test_body_transform_resolves_id_only_before_unique_title_fallback():
    pages = [{"id": "page-id", "title": "Canonical title"}]

    markdown, references = wiki_browser.transform_wiki_body("[[page-id]]", pages)

    assert markdown == "**Canonical title**"
    assert [(page["id"], label) for page, label in references] == [("page-id", "Canonical title")]


def test_body_transform_preserves_fenced_code_references():
    body = "Text [[page-1|Page one]] and `[[page-2|inline]]`.\n```md\n[[page-2|literal]]\n```"
    markdown, references = wiki_browser.transform_wiki_body(body, [{"id": "page-1", "title": "Page one"},
                                                                    {"id": "page-2", "title": "Page two"}])

    assert "Page one" in markdown
    assert "[[page-2|literal]]" in markdown
    assert "`[[page-2|inline]]`" in markdown
    assert [page["id"] for page, _ in references] == ["page-1"]


def test_body_renderer_deduplicates_navigation_buttons_by_page_id():
    class FakeStreamlit:
        def __init__(self):
            self.session_state = {}
            self.buttons = []

        def markdown(self, _value):
            pass

        def button(self, label, **kwargs):
            self.buttons.append((label, kwargs))
            return False

    fake = FakeStreamlit()
    page = {"id": "target", "title": "Target"}
    wiki_browser.render_wiki_body("[[target]] then [[target|again]]", [page], st_module=fake)

    assert len(fake.buttons) == 1


def test_read_renderer_shows_resolved_body_reference_buttons_and_navigates():
    class FakeStreamlit:
        def __init__(self):
            self.session_state = {}
            self.markdown_calls = []
            self.buttons = []
            self.reruns = 0

        def markdown(self, value):
            self.markdown_calls.append(value)

        def button(self, label, **kwargs):
            self.buttons.append((label, kwargs))
            return True

        def rerun(self):
            self.reruns += 1

    fake = FakeStreamlit()
    wiki_browser.render_wiki_body("See [[target|Target label]].", [{"id": "target", "title": "Target"}], st_module=fake)

    assert fake.markdown_calls == ["See **Target label**."]
    assert len(fake.buttons) == 1
    assert fake.session_state == {"agent_wiki_selected_page_id": "target", "wiki_editor_mode": "read"}
    assert fake.reruns == 1
    assert '_render_selected_evidence(evidence, pages_all)' in inspect.getsource(wiki_browser.render_wiki_tab)
    assert 'render_wiki_body(evidence["body"], pages, st_module=st_module)' in inspect.getsource(wiki_browser._render_selected_evidence)
