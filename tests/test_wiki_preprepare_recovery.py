import hashlib
import json

import pytest

from agent_console import wiki
from reports.wiki_source_journal import Journal
from test_wiki_source_pipeline import NOW, module


def _rejected_targets_with_new_page(pipeline_env):
    pipeline = module()
    original = pipeline_env["llm"]
    model_calls = {"compile": 0, "route": 0, "propose": 0}

    def llm(prompt):
        kind = ("compile" if prompt.startswith("Compile") else
                "route" if prompt.startswith("Route") else
                "propose" if prompt.startswith("Propose") else "other")
        model_calls[kind] = model_calls.get(kind, 0) + 1
        if prompt.startswith("Route"):
            data = json.loads(prompt.rsplit("\n", 1)[1])
            assert data["candidates"]
            return json.dumps({"selected": [],
                "rejected": {candidate["page_id"]: "synthetic no-match"
                             for candidate in data["candidates"]}, "new_page_specs": [{
                "title": "Synthetic capacity topic", "kind": "concept", "surface": "market",
                "purpose": "Synthetic recovery target",
            }]})
        if prompt.startswith("Propose"):
            data = json.loads(prompt.rsplit("\n", 1)[1])
            claim = data["compiled"]["claims"][0]
            return json.dumps({"targets": [{**spec, "before_hash": None, "additions": [{
                "claim_ids": [claim["claim_id"]], "as_of": claim["as_of"],
                "scope": claim["scope"], "body": "Added capacity; demand uncertain [S1].",
            }], "relations": []} for spec in data["new_page_specs"]]})
        return original(prompt)

    return pipeline, llm, model_calls


def _assert_one_summary_and_log(pipeline_env):
    revision = pipeline_env["source"]["revision_id"]
    summary_id = hashlib.sha256(("compiled-source:" + revision).encode()).hexdigest()
    summary = wiki.get_page(summary_id)
    assert summary is not None
    assert any(claim.get("source_revision_id") == revision
               for claim in (summary.get("source_knowledge") or {}).get("claims", []))
    journal = module().Journal()
    operations = list((journal.root / "operations").glob("*.json"))
    assert len(operations) == 1
    operation = journal._read(operations[0])
    assert operation["stage"] == "committed"
    assert operation["log"] == "done"
    log = (wiki.wiki_artifacts_dir() / "log.md").read_text()
    assert sum('"action": "source_ingest"' in line for line in log.splitlines()) == 1


def test_retry_after_routing_saved_before_prepare_publishes_once(
        pipeline_env, monkeypatch):
    pipeline, llm, model_calls = _rejected_targets_with_new_page(pipeline_env)
    original_prepare = Journal.prepare
    crashed = []

    def fail_before_prepare(self, batch):
        if not crashed:
            crashed.append(True)
            raise SystemExit("synthetic crash before prepare")
        return original_prepare(self, batch)

    monkeypatch.setattr(Journal, "prepare", fail_before_prepare)
    with pytest.raises(SystemExit, match="synthetic crash before prepare"):
        pipeline.run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)

    journal = pipeline.Journal()
    source = journal.load_source(pipeline_env["source"]["revision_id"])
    assert source["routing"]["support"]["integrated"] is False
    assert source["routing"]["support"]["examined"]
    assert source["routing"]["support"]["selected"] == []
    assert model_calls == {"compile": 1, "route": 1, "propose": 1}
    assert list((journal.root / "operations").glob("*.json")) == []
    assert wiki.get_page(hashlib.sha256(("compiled-source:" + pipeline_env["source"]["revision_id"]).encode()).hexdigest()) is None
    assert '"action": "source_ingest"' not in (wiki.wiki_artifacts_dir() / "log.md").read_text()

    result = pipeline.run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)
    assert result["data_committed"] == 1
    assert model_calls == {"compile": 1, "route": 1, "propose": 2}
    _assert_one_summary_and_log(pipeline_env)

    repeat = pipeline.run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)
    assert repeat["data_committed"] == 0
    assert model_calls == {"compile": 1, "route": 1, "propose": 2}
    _assert_one_summary_and_log(pipeline_env)


def test_failure_after_prepare_recovers_existing_operation_once(
        pipeline_env, monkeypatch):
    pipeline, llm, model_calls = _rejected_targets_with_new_page(pipeline_env)
    original_prepare = Journal.prepare
    crashed = []

    def fail_after_prepare(self, batch):
        operation = original_prepare(self, batch)
        if not crashed:
            crashed.append(True)
            raise SystemExit("synthetic crash after prepare")
        return operation

    monkeypatch.setattr(Journal, "prepare", fail_after_prepare)
    with pytest.raises(SystemExit, match="synthetic crash after prepare"):
        pipeline.run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)

    journal = pipeline.Journal()
    operations = list((journal.root / "operations").glob("*.json"))
    assert len(operations) == 1
    operation_id = operations[0].stem
    assert journal._read(operations[0])["stage"] == "prepared"
    assert model_calls == {"compile": 1, "route": 1, "propose": 1}
    assert wiki.get_page(hashlib.sha256(("compiled-source:" + pipeline_env["source"]["revision_id"]).encode()).hexdigest()) is None

    result = pipeline.run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)
    assert result["data_committed"] == 1
    assert model_calls == {"compile": 1, "route": 1, "propose": 1}
    assert [path.stem for path in (journal.root / "operations").glob("*.json")] == [operation_id]
    _assert_one_summary_and_log(pipeline_env)

    repeat = pipeline.run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)
    assert repeat["data_committed"] == 0
    assert model_calls == {"compile": 1, "route": 1, "propose": 1}
    assert [path.stem for path in (journal.root / "operations").glob("*.json")] == [operation_id]
    _assert_one_summary_and_log(pipeline_env)
