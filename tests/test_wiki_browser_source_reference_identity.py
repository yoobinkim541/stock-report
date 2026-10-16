from collections import Counter

from dashboard import wiki_browser


URL = "https://example.com/article/" + "a" * 700
PATH = "/home/ubuntu/reports/text/telegram/2026/10/08/" + "p" * 160 + ".txt"


def test_selected_evidence_retains_full_clickable_identifiers():
    model = wiki_browser.build_selected_evidence_model({"id": "source", "title": "Source",
                                                       "source_refs": [URL, PATH]})
    assert model["evidence"] == [URL, PATH]


def test_legacy_record_adapter_preserves_full_archive_pointer():
    page = wiki_browser._record_to_page({"id": "legacy", "title": "Legacy source",
                                          "artifacts": [URL, PATH]})
    assert page["source_refs"] == [URL, PATH]


def test_browser_keeps_every_reference_from_a_valid_core_page():
    refs = [f"https://example.com/source/{number}" for number in range(20)]
    model = wiki_browser.build_selected_evidence_model({"id": "many", "title": "Many sources",
                                                       "source_refs": refs})
    assert model["evidence"] == refs


def test_browser_does_not_count_shared_long_url_prefix_as_shared_evidence():
    a = wiki_browser._normalize_page({"id": "a", "title": "Alpha", "source_refs": [URL + "A"]})
    b = wiki_browser._normalize_page({"id": "b", "title": "Beta", "source_refs": [URL + "B"]})
    counter = Counter([URL + "A", URL + "B"])
    score = wiki_browser._related_score(a, b, counter=counter)
    assert score[1] == 0


def test_oversized_reference_cannot_become_a_truncated_source_backed_url():
    model = wiki_browser.build_selected_evidence_model({"id": "too-long", "title": "Oversized",
        "source_refs": ["https://example.com/" + "a" * 5000]})
    assert model["evidence"] == []
    assert model["verification_status"] == "unverified"


def test_browser_keeps_sensitive_reference_values_redacted():
    model = wiki_browser.build_selected_evidence_model({"id": "private", "title": "Private ref",
        "source_refs": ["https://example.com?token=private-value", "/home/ubuntu/.ssh/private-key"]})
    assert "private-value" not in " ".join(model["evidence"])
    assert "private-key" not in " ".join(model["evidence"])


def test_dataclass_page_uses_the_same_reference_safety_contract():
    page = wiki_browser.WikiPage(id="typed", title="Typed source",
        source_refs=(URL, "https://example.com/" + "x" * 5000))
    assert wiki_browser.build_selected_evidence_model(page)["evidence"] == [URL]


def test_dataclass_rejected_pointer_does_not_claim_source_backing():
    page = wiki_browser.WikiPage(id="invalid", title="Invalid source",
        source_refs=("https://example.com/" + "x" * 5000,))
    assert wiki_browser.build_selected_evidence_model(page)["verification_status"] == "unverified"
