import hashlib
import json

import pytest

from reports.wiki_source_journal import Journal, _encoded


def batch():
    proposal = {"protocol_version": 3, "revision_id": "a" * 64, "role": "support",
                "observed_evidence_ids": ["e1"], "context": {"notes": [], "dependencies": [], "omitted": []},
                "dependencies": [], "targets": [], "refusal": "insufficient", "source_map": {}}
    operation_id = hashlib.sha256(_encoded(proposal).encode()).hexdigest()
    return {"protocol_version": 3, "operation_id": operation_id, "proposal": proposal,
            "revision_ids": [], "source_map": {}, "dependencies": [], "source_inputs": {},
            "public_proof": {"export_metadata": {"protocol_version": 3, "source_note_ids": [], "target_ids": []},
                             "source_assets": {}}, "original_claims": {},
            "expected_before": {}, "expected_after": {}, "target_ids": [], "upserts": [],
            "snapshots": [], "prepared_at": "2026-10-20T00:00:00+00:00"}


def test_v3_receipt_is_isolated_and_strict(tmp_path):
    journal = Journal(tmp_path / "journal")
    frozen = batch()
    receipt = journal.prepare_topic_v3(frozen)
    assert receipt["schema_version"] == 3
    assert journal.load_topic_operation_v3(frozen["operation_id"]) == receipt
    assert journal.load_topic_operation(frozen["operation_id"]) is None
    assert journal.pending_topic_operations_v3() == [receipt]
    with pytest.raises(ValueError, match="topic_protocol_invalid"):
        journal.prepare_topic_v3({**frozen, "protocol_version": 4})
    with pytest.raises(ValueError, match="topic_protocol_invalid"):
        journal.prepare_topic_v3({**frozen, "protocol_version": 3.0})
    path = journal._path("topic-operations-v3", frozen["operation_id"])
    broken = json.loads(path.read_text())
    broken["batch"]["source_inputs"] = {"forged": True}
    path.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="topic_journal_corrupt"):
        journal.load_topic_operation_v3(frozen["operation_id"])


def test_legacy_topic_transition_cannot_override_to_v3(tmp_path):
    journal = Journal(tmp_path / "journal")
    frozen = batch()
    journal.prepare_topic_v3(frozen)
    with pytest.raises(ValueError, match="topic_protocol_invalid"):
        journal.transition_topic(frozen["operation_id"], version=3, stage="committed")
    assert journal.load_topic_operation_v3(frozen["operation_id"])["stage"] == "prepared"


def test_v3_and_v2_receipts_are_not_interchangeable_across_directories(tmp_path):
    journal = Journal(tmp_path / "journal")
    frozen = batch()
    receipt = journal.prepare_topic_v3(frozen)
    copied_v3 = journal._path("topic-operations", frozen["operation_id"])
    copied_v3.parent.mkdir(parents=True, exist_ok=True)
    copied_v3.write_text(_encoded(receipt))
    with pytest.raises(ValueError, match="topic_journal_corrupt"):
        journal.load_topic_operation(frozen["operation_id"])

    legacy_proposal = {**frozen["proposal"], "protocol_version": 2}
    legacy_id = hashlib.sha256(_encoded(legacy_proposal).encode()).hexdigest()
    legacy_batch = {**frozen, "protocol_version": 2, "operation_id": legacy_id,
                    "proposal": legacy_proposal, "target_ids": ["topic"],
                    "upserts": [{"id": "topic"}], "expected_before": {"topic": None},
                    "expected_after": {"topic": "a" * 64}}
    journal.prepare_topic(legacy_batch)
    copied_v2 = journal._path("topic-operations-v3", legacy_id)
    copied_v2.parent.mkdir(parents=True, exist_ok=True)
    copied_v2.write_text(_encoded(journal.load_topic_operation(legacy_id)))
    with pytest.raises(ValueError, match="topic_journal_corrupt"):
        journal.load_topic_operation_v3(legacy_id)


@pytest.mark.parametrize("contents", [
    '{"schema_version":3,"schema_version":3}',
    '{"schema_version":3,"batch":NaN}',
])
def test_v3_loader_rejects_ambiguous_or_nonfinite_json(tmp_path, contents):
    journal = Journal(tmp_path / "journal")
    frozen = batch()
    path = journal._path("topic-operations-v3", frozen["operation_id"])
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    with pytest.raises(ValueError, match="topic_journal_corrupt"):
        journal.load_topic_operation_v3(frozen["operation_id"])
