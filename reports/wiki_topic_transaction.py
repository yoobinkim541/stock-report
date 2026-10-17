"""Inactive v2 topic transactions. No source writes or legacy recovery dispatch.

Preparation journals a frozen batch, but does not publish data. Recovery is the
only commit boundary. It returns operation_id, stage, effects, reason and
newly_committed; committed retries repair effects without reappending data.

Fix-round ruling: receipt checksums detect corruption, not authenticity. Before
commit, rebuild the complete batch from validated evidence and fresh before rows
at prepared_at. A separate internal non-wiki witness is written in that same
guarded store transaction, binding crash recovery to the validated frozen batch.
Bare topic markers and receipt stage/after hashes are never commit proof. The
shared store remains authoritative; this is not authentication against an actor
able to rewrite both receipts and arbitrary store commit witnesses. Unwitnessed
old v2 committed receipts fail closed; v1 storage/recovery is unchanged.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import os

from agent_console import shared_memory, wiki
from reports.wiki_source_journal import Journal, _encoded
from reports.wiki_topic_context import _note, _verified_source, revalidate_topic_context
from reports.wiki_topic_integration import _current, validate_topic_integration


def _digest(value):
    return hashlib.sha256(_encoded(value).encode()).hexdigest()


def _rows():
    return shared_memory._read_jsonl_checked(shared_memory._paths()["events"])


@contextmanager
def _locks(proposal, journal, extra_pages=()):
    # All topic workers use source locks first, then globally sorted page locks;
    # this also agrees with the legacy single-source worker's lock hierarchy.
    revisions = sorted({v["revision_id"] for v in proposal["source_map"].values()})
    pages = [t["page_id"] for t in proposal["targets"]]
    pages.extend(d["page_id"] for d in proposal["dependencies"])
    with ExitStack() as stack:
        for revision in revisions:
            stack.enter_context(journal.source_lock(revision))
        rows = _rows()
        parents = set(pages)
        explicit = {tag.split(":", 1)[1] for row in rows if row["id"] in parents
                    for tag in row.get("tags", []) if tag.startswith("split_into:")}
        pages.extend(row["id"] for row in rows if row.get("parent_page_id") in parents
                     or row["id"] in explicit or any(f"split_from:{pid}" in row.get("tags", []) for pid in parents))
        stack.enter_context(journal.target_locks([*pages, *extra_pages]))
        yield


def _check(proposal, *, journal, records, now):
    context = {"notes": [], "dependencies": deepcopy(proposal["dependencies"]), "omitted": []}
    for dep in proposal["dependencies"]:
        note, _ = _note(dep["page_id"], dep["revision_id"], journal=journal, records=records,
                        now=now, surface=dep["surface"])
        context["notes"].append(note)
    revalidate_topic_context(context, journal=journal, records=records, now=now)
    source, compiled, receipt = _verified_source(journal, proposal["revision_id"], now)
    source["role"] = proposal["role"]
    if source["role"] not in receipt["evidence_by_role"]:
        raise ValueError("topic_role_changed")
    _current(compiled, source)
    payload = {"refusal": proposal["refusal"], "targets": []}
    snapshots = []
    for target in proposal["targets"]:
        keys = ["page_id", "before_hash", "contributing_revision_ids", "scope_explanation", "additions", "relations"]
        item = {k: deepcopy(target[k]) for k in keys}
        if target["before_hash"] is None:
            if any(r["id"] == target["page_id"] for r in records):
                raise ValueError("topic_target_changed")
            item["page_id"] = None
            item.update({k: target[k] for k in ("title", "kind", "surface", "purpose")})
        else:
            snapshots.append(wiki.semantic_snapshot(target["page_id"], records=records))
        payload["targets"].append(item)
    checked = validate_topic_integration(payload, compiled=compiled, source=source, context=context, targets=snapshots)
    if checked != proposal or not checked["targets"]:
        raise ValueError("topic_proposal_changed")
    evidence = {}
    for entry in proposal["source_map"].values():
        revision = entry["revision_id"]
        verified, _, stored = _verified_source(journal, revision, now)
        if verified["canonical_url"] != entry["canonical_url"]:
            raise ValueError("topic_source_changed")
        evidence[revision] = {"raw_sha256": verified["raw_sha256"], "text_sha256": verified["text_sha256"],
                              "compile_sha256": stored["compile_sha256"]}
    return snapshots, evidence


def _unique(values):
    result = []
    for value in values:
        if value not in result:
            result.append(deepcopy(value))
    return result


def _build(proposal, snapshots, *, records, journal, now, operation_id):
    by_id = {r["id"]: r for r in records}
    snaps = {s["page_id"]: s for s in snapshots}
    upserts = []
    for target in proposal["targets"]:
        page_id = target["page_id"]
        snap = snaps.get(page_id)
        page = deepcopy(snap["page"]) if snap else {"id": page_id, "title": target["title"],
            "kind": "concept", "surface": target["surface"], "status": "draft"}
        raw = page.pop("raw", {})
        page["body"] = (snap["body"] if snap else "") + "".join(
            f"\n\n### {a['as_of']} | {a['scope']}\n{a['body']}" for a in target["additions"])
        page.update(status="draft", updated_at=now.isoformat(), _preserve_body_exact=True)
        refs = list(snap["source_refs"] if snap else [])
        support = list(snap["evidence_ids"] if snap else [])
        conflict = list(snap["conflicting_evidence_ids"] if snap else [])
        knowledge = deepcopy(snap["source_knowledge"] if snap else {})
        integrations = list(knowledge.get("integrations", []))
        for revision in target["contributing_revision_ids"]:
            stored = journal.load_source(revision)
            source = stored["source"]
            refs.extend(source[k] for k in ("canonical_url", "raw_ref", "text_ref"))
            for role, ids in stored["evidence_by_role"].items():
                (conflict if role == "conflict" else support).extend(ids)
                integrations = _unique([*integrations, {"revision_id": revision, "role": role,
                    "evidence_ids": ids, "relation_state": "unresolved",
                    "relation_note": "Relationships are not factual adjudication."}])
        refs, support, conflict = map(_unique, (refs, support, conflict))
        claims_by_id = {c["claim_id"]: deepcopy(c) for c in (snap["claims"] if snap else [])}
        # Preserve historical extensions while adding server-derived competitors.
        for claim in target["original_claims"]:
            prior = claims_by_id.get(claim["claim_id"], {})
            claims_by_id[claim["claim_id"]] = {**prior, **deepcopy(claim),
                "competing_claim_ids": _unique([
                    *prior.get("competing_claim_ids", []), *claim["competing_claim_ids"]])}
        claims = list(claims_by_id.values())
        knowledge.update(claims=claims[:100], limitations=_unique([
            *knowledge.get("limitations", []), *target["limitations"]]),
            relations=_unique([*knowledge.get("relations", []), *target["relations"]]), integrations=integrations)
        knowledge["topic_operations"] = _unique([*knowledge.get("topic_operations", []), operation_id])
        revisions = {claim["source_revision_id"] for claim in claims}
        summaries = {dependency["page_id"] for dependency in proposal["dependencies"]
                     if dependency["revision_id"] in revisions}
        summaries.update(hashlib.sha256(("compiled-source:" + revision).encode()).hexdigest()
                         for revision in revisions)
        summaries = {identity for identity in summaries if identity in by_id
                     and "compiled_source" in by_id[identity].get("tags", [])
                     and not by_id[identity].get("parent_page_id")}
        # Reverse navigation is derived by wiki._apply_backlinks. Do not mutate
        # read-only summaries: their immutable fingerprints bind public receipts.
        page["links"] = wiki._clean_links([*page.get("links", []), *sorted(summaries)], self_id=page_id)
        page.update(source_refs=refs[:20], evidence_ids=support[:100], conflicting_evidence_ids=conflict[:100],
                    source_knowledge=knowledge)
        pure = wiki._prepare_split_records(page, records=records)
        if not pure:
            pure = [{**raw, **wiki._build_wiki_record(page, existing=raw)}]
        count = max((len(refs) + 19) // 20, (len(support) + 99) // 100,
                    (len(conflict) + 99) // 100, (len(claims) + 99) // 100)
        if len(refs) > 20 or len(support) > 100 or len(conflict) > 100 or len(claims) > 100:
            for index in range(count):
                child_id = "prov-" + hashlib.sha256(f"{page_id}:{operation_id}:{index}".encode()).hexdigest()
                child_refs = refs[index * 20:(index + 1) * 20]
                child = wiki._build_wiki_record({"id": child_id, "title": f"{page['title']} provenance [{index + 1:04d}]",
                    "body": "\n".join(child_refs) or "Claim and evidence provenance", "kind": "note",
                    "surface": page["surface"], "status": "draft", "parent_page_id": page_id,
                    "source_refs": child_refs, "links": [page_id], "updated_at": now.isoformat(),
                    "evidence_ids": support[index * 100:(index + 1) * 100],
                    "conflicting_evidence_ids": conflict[index * 100:(index + 1) * 100],
                    "source_knowledge": {"claims": claims[index * 100:(index + 1) * 100]}}, existing={})
                child["provenance_only"] = True
                pure.append(child)
        for row in pure:
            old = by_id.get(row["id"])
            if old and row["id"] != page_id and old.get("parent_page_id") != page_id and f"split_from:{page_id}" not in old.get("tags", []):
                raise ValueError("child_identity_collision")
            row["updatedAt"] = now.isoformat()
            row["createdAt"] = (old or {}).get("createdAt") or now.isoformat()
            upserts.append(shared_memory.normalize_record(row))
    if not upserts or len({r["id"] for r in upserts}) != len(upserts):
        raise ValueError("batch_identity_invalid")
    return upserts


def _freeze(proposal, *, journal, records, now, prepared_at):
    """Derive the entire write set, never accepting journal-supplied writes."""
    fixed_time = datetime.fromisoformat(prepared_at)
    if fixed_time.tzinfo is None or fixed_time > now:
        raise ValueError("topic_prepared_time_invalid")
    operation_id = _digest(proposal)
    snapshots, evidence = _check(proposal, journal=journal, records=records, now=now)
    upserts = _build(proposal, snapshots, records=records, journal=journal,
                     now=fixed_time, operation_id=operation_id)
    ids = {r["id"] for r in upserts}
    dependencies = {d["page_id"] for d in proposal["dependencies"]}
    if ids & dependencies:
        raise ValueError("topic_readonly_target")
    before = {pid: wiki._source_fingerprint(pid, records) for pid in sorted(ids | dependencies)}
    projected = [r for r in records if r["id"] not in ids] + upserts
    return {"protocol_version": 2, "operation_id": operation_id, "proposal": proposal,
        "revision_ids": sorted({revision for t in proposal["targets"] for revision in t["contributing_revision_ids"]}),
        "source_map": proposal["source_map"], "dependencies": proposal["dependencies"],
        "source_hashes": evidence,
        "original_claims": {t["page_id"]: t["original_claims"] for t in proposal["targets"]},
        "target_ids": [t["page_id"] for t in proposal["targets"]], "expected_before": before,
        "expected_after": {pid: wiki._source_fingerprint(pid, projected) for pid in sorted(ids)},
        "upserts": upserts, "snapshots": [deepcopy(r) for r in records if r["id"] in ids],
        "prepared_at": prepared_at}


def _commit_record(batch):
    # A store-side historical witness, NOT authentication of arbitrary file
    # contents. Only the validated atomic commit creates it. Receipt hashes and
    # editable topic_operations markers alone never authorize recovery.
    return shared_memory.normalize_record({"id": "topic-commit:" + batch["operation_id"],
        "title": "Topic transaction commit", "tags": ["topic-commit"],
        "createdAt": batch["prepared_at"], "updatedAt": batch["prepared_at"],
        "topic_operation_id": batch["operation_id"], "topic_batch_sha256": _digest(batch),
        "source": {"surface": "internal", "provider": "topic-transaction", "writer": "topic-transaction"}})


def _apply_with_witness(batch):
    commit = deepcopy(batch)
    witness = _commit_record(batch)
    commit["upserts"].append(witness)
    commit["expected_before"][witness["id"]] = None
    commit["expected_after"][witness["id"]] = wiki._source_fingerprint(witness["id"], [witness])
    return wiki.apply_source_batch(commit)


def prepare_topic_batch(proposal: dict, *, journal: Journal, records: list[dict], now: datetime) -> dict:
    """Return and persist a frozen v2 batch; never modify wiki/source data.

    Keys: protocol_version, operation_id, proposal, revision_ids, source_map,
    dependencies, source_hashes, original_claims (by target), target_ids,
    expected_before/after, upserts, snapshots, prepared_at. Before includes
    read-only dependencies; after covers data targets including split children.
    """
    proposal = deepcopy(proposal)
    operation_id = _digest(proposal)
    with journal.target_locks(["topic-operation:" + operation_id]):
        existing = journal.load_topic_operation(operation_id)
        if existing:
            if existing["batch"]["proposal"] != proposal:
                raise ValueError("operation_conflict")
            return existing["batch"]
        with _locks(proposal, journal):
            _check(proposal, journal=journal, records=records, now=now)
            fresh = _rows()
            batch = _freeze(proposal, journal=journal, records=fresh, now=now, prepared_at=now.isoformat())
            journal.prepare_topic(batch)
            return batch


def _effects(receipt, journal):
    from agent_console.wiki_publication_config import enabled
    if enabled():
        from reports import wiki_publication_effects
        return wiki_publication_effects.dispatch_effects(receipt, journal)
    op_id = receipt["operation_id"]
    for effect in ("artifacts", "search", "log"):
        if receipt[effect] == "done":
            continue
        try:
            if effect == "artifacts":
                with shared_memory._events_lock():
                    _rows()
                    shared_memory._write_index_locked()
                if not wiki.rebuild_artifacts(operation_id=op_id).get("ok"):
                    raise ValueError("artifact_refresh_failed")
            elif effect == "search":
                if not wiki.sync_qmd().get("ok"):
                    raise ValueError("search_refresh_failed")
            else:
                titles = [row["title"] for row in receipt["batch"]["upserts"]
                          if row["id"] in receipt["batch"]["target_ids"]]
                wiki._append_wiki_log("topic_ingest", operation_id=op_id,
                    title="; ".join(titles)[:160],
                    page_ids=receipt["batch"]["target_ids"], revision_ids=receipt["batch"]["revision_ids"])
        except Exception:
            receipt = journal.transition_topic(op_id, effects={effect: "failed"}, error_code=effect + "_refresh_failed")
        else:
            remaining_failed = any(receipt[k] == "failed" for k in ("artifacts", "search", "log") if k != effect)
            receipt = journal.transition_topic(op_id, effects={effect: "done"},
                error_code=receipt["error_code"] if remaining_failed else "")
    return receipt


def recover_topic_operation(operation_id: str, *, journal: Journal, now: datetime) -> dict:
    """Apply data atomically once, or resume only idempotent publication effects."""
    with journal.target_locks(["topic-operation:" + operation_id]):
        receipt = journal.load_topic_operation(operation_id)
        if receipt is None:
            raise ValueError("topic_operation_unavailable")
        batch = receipt["batch"]
        was_committed = receipt["stage"] == "committed"
        witness = _commit_record(batch)
        with _locks(batch["proposal"], journal, [*batch["expected_before"], witness["id"]]):
            rows = _rows()
            witnesses = [r for r in rows if r["id"] == witness["id"]]
            witnessed = witnesses == [witness]
            if receipt["stage"] == "committed" and not witnessed:
                raise ValueError("topic_commit_witness_mismatch")
            if receipt["stage"] in {"prepared", "applied"}:
                if not witnessed:
                    try:
                        rebuilt = _freeze(batch["proposal"], journal=journal, records=rows, now=now,
                                          prepared_at=batch["prepared_at"])
                        if witnesses or rebuilt != batch:
                            raise ValueError("topic_frozen_batch_changed")
                    except (ValueError, OSError, KeyError, TypeError, UnicodeError):
                        receipt = journal.transition_topic(operation_id, stage="stale-base", error_code="topic_dependency_changed")
                    else:
                        result = _apply_with_witness(rebuilt)
                        if result["status"] != "applied":
                            receipt = journal.transition_topic(operation_id, stage="stale-base", error_code="topic_target_changed")
                if receipt["stage"] != "stale-base":
                    receipt = journal.transition_topic(operation_id, stage="committed")
            if receipt["stage"] == "committed":
                receipt = _effects(receipt, journal)
        return {"operation_id": operation_id, "stage": receipt["stage"],
            "effects": {k: receipt[k] for k in ("artifacts", "search", "log")},
            "reason": receipt["error_code"], "newly_committed": receipt["stage"] == "committed" and not was_committed}
