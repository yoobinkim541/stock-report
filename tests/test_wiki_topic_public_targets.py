"""Public eligibility of committed multi-source topics, never private records."""
import copy

from agent_console import shared_memory
from scripts.run_public_wiki_pilot import public_target_ids
from test_wiki_topic_transaction import prepare, recover


def test_committed_topic_is_eligible_only_with_all_public_revisions(transaction):
    _, journal, _ = transaction
    batch = prepare(transaction)
    recover(batch, journal)
    allowed = set(batch["revision_ids"])
    assert set(batch["target_ids"]) <= public_target_ids(journal, allowed)
    assert not set(batch["target_ids"]) & public_target_ids(journal, {next(iter(allowed))})


def test_uncommitted_topic_is_not_public_candidate(transaction):
    _, journal, _ = transaction
    batch = prepare(transaction)
    assert not set(batch["target_ids"]) & public_target_ids(journal, set(batch["revision_ids"]))


def test_changed_topic_or_missing_commit_witness_is_not_public(transaction):
    _, journal, _ = transaction
    batch = prepare(transaction)
    recover(batch, journal)
    rows = shared_memory.inspect_records()
    changed = copy.deepcopy(rows)
    next(r for r in changed if r["id"] == batch["target_ids"][0])["body"] += " private edit"
    assert not set(batch["target_ids"]) & public_target_ids(
        journal, set(batch["revision_ids"]), records=changed)
    without_witness = [r for r in rows if r["id"] != "topic-commit:" + batch["operation_id"]]
    assert not set(batch["target_ids"]) & public_target_ids(
        journal, set(batch["revision_ids"]), records=without_witness)


def test_missing_contributing_source_receipt_is_not_public(transaction):
    _, journal, _ = transaction
    batch = prepare(transaction)
    recover(batch, journal)
    journal._path("sources", batch["revision_ids"][0]).unlink()
    assert not set(batch["target_ids"]) & public_target_ids(journal, set(batch["revision_ids"]))
