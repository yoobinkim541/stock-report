from __future__ import annotations


def test_usage_records_retrieval_and_context_idempotently(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_EVIDENCE_USAGE_PATH", str(tmp_path / "usage.jsonl"))
    from agent_console import evidence_usage

    evidence_usage.record_retrieval("q1", ["p1", "p2"], "qmd", False)
    evidence_usage.record_retrieval("q1", ["p1", "p2"], "qmd", False)
    evidence_usage.record_context_use("q1", ["p2", "not-returned"])
    evidence_usage.record_context_use("q1", ["p2"])

    summary = evidence_usage.usage_summary()

    assert summary["retrieval_count"] == 1
    assert summary["context_use_count"] == 1
    assert summary["retrieved_page_count"] == 2
    assert summary["context_page_count"] == 1
    assert summary["unused_retrieved_page_count"] == 1
    assert summary["retrieval_to_context_ratio"] == 0.5


def test_usage_summary_reports_fallback_queries(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_EVIDENCE_USAGE_PATH", str(tmp_path / "usage.jsonl"))
    from agent_console import evidence_usage

    evidence_usage.record_retrieval("q2", ["p3"], "fallback", True)

    summary = evidence_usage.usage_summary()

    assert summary["fallback_retrieval_count"] == 1
    assert summary["fallback_ratio"] == 1.0
