"""Opt-in source compilation worker with durable, independently recoverable stages."""
from __future__ import annotations

import argparse
from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Callable

import safe_io
from agent_console import shared_memory, wiki
from reports import source_collector, wiki_source_revision
from reports.wiki_source_compiler import compile_source, compilation_failure_code
from reports.wiki_source_integration import discover_targets, route_pass, hydrate_target, propose_integration, _new_spec
from reports.wiki_source_journal import Journal
from reports.wiki_source_chunks import CHUNK_CHARS, ChunkingPending, compile_chunks


def _mode_enabled() -> bool:
    return os.getenv("WIKI_SOURCE_PIPELINE_ENABLED", "0") == "1"


def run_public_topic(source: dict, *, journal: Journal, llm_fn,
                     reserved_revision_id: str, now: datetime) -> dict:
    """Reserved public stage; call only after the legacy source lock is released."""
    if os.getenv("AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED", "0") != "1":
        return {"enabled": False}
    from scripts.run_public_wiki_pilot import is_public, validate_public_targets
    from scripts.run_public_wiki_rollout import public_revisions
    from reports.wiki_topic_context import select_topic_context, _verified_source
    from reports.wiki_topic_integration import propose_topic_integration
    from reports.wiki_topic_transaction import prepare_topic_batch, recover_topic_operation

    if source.get("revision_id") != reserved_revision_id:
        raise ValueError("unreserved_source")
    if not is_public(source):
        raise ValueError("private_source_disallowed")
    for path in sorted((journal.root / "topic-operations").glob("*.json")):
        receipt = journal.load_topic_operation(path.stem)
        if (receipt["batch"]["proposal"]["revision_id"] == reserved_revision_id
                and receipt["batch"]["proposal"]["role"] == source["role"]):
            if receipt["stage"] in {"prepared", "applied", "committed"}:
                return {"enabled": True, **recover_topic_operation(path.stem, journal=journal, now=now)}
    verified, compiled, _ = _verified_source(journal, reserved_revision_id, now)
    verified["role"] = source["role"]
    rows = shared_memory.inspect_records()
    context = select_topic_context(compiled, journal=journal, records=rows, now=now, surface="market")
    if not context["notes"]:
        return {"enabled": True, "stage": "insufficient_context"}
    state = discover_targets(compiled, surface="market")
    targets = [hydrate_target(identity) for identity in state["candidate_ids"]]
    targets = [snap for snap in targets if snap["page"]["kind"] == "concept"]
    validate_public_targets(targets, journal, public_revisions(journal))
    proposal = propose_topic_integration(compiled, verified, context, targets,
        llm_fn=llm_fn, journal=journal, records=rows, now=now)
    if proposal["refusal"]:
        return {"enabled": True, "stage": "refused"}
    batch = prepare_topic_batch(proposal, journal=journal, records=rows, now=now)
    return {"enabled": True, **recover_topic_operation(batch["operation_id"], journal=journal, now=now)}


def _record_integration(receipt: dict, journal: Journal) -> None:
    batch = receipt["batch"]
    source = journal.load_source(receipt["revision_id"])
    if source is None or "routing_state" not in batch:
        return
    role = batch["role"]
    state = deepcopy(batch["routing_state"])
    previous = source["routing"].get(role, {})
    same_pass = all(previous.get(key) == state.get(key) for key in (
        "cursor", "examined", "selected", "unexamined", "new_page_specs", "surface"))
    completes_current_pass = previous.get("cursor", 0) < state.get("cursor", 0) or same_pass
    if previous.get("cursor", 0) >= state.get("cursor", 0):
        state = deepcopy(previous)
    state["integrated_evidence_ids"] = list(dict.fromkeys([
        *previous.get("integrated_evidence_ids", []), *batch["observed_evidence_ids"]]))
    if completes_current_pass:
        state["integrated"] = True
    journal.save_routing(receipt["revision_id"], role, state)


def _repair_effects(receipt: dict, journal: Journal) -> dict:
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
                    shared_memory._read_jsonl_checked(shared_memory._paths()["events"])
                    shared_memory._write_index_locked()
                result = wiki.rebuild_artifacts(operation_id=op_id)
                if not result.get("ok"):
                    raise ValueError("artifact_refresh_failed")
            elif effect == "search":
                result = wiki.sync_qmd()
                if not result.get("ok"):
                    raise ValueError("search_refresh_failed")
            else:
                wiki._append_wiki_log("source_ingest", operation_id=op_id,
                                     page_ids=receipt["batch"]["target_ids"], revision_id=receipt["revision_id"])
        except Exception:
            receipt = journal.transition(op_id, effects={effect: "failed"}, error_code=effect + "_refresh_failed")
        else:
            receipt = journal.transition(op_id, effects={effect: "done"})
    return receipt


def _recover_locked(receipt: dict, journal: Journal) -> dict:
    # The source work lock is already held. Re-read after acquiring it, since a
    # different recovery process may have finished this same operation.
    current = journal._read(journal._path("operations", receipt["operation_id"]))
    if current is None or current.get("revision_id") != receipt["revision_id"]:
        raise ValueError("journal_corrupt")
    receipt = current
    was_committed = receipt["stage"] == "committed"
    batch = receipt["batch"]
    with journal.target_locks(list(batch["expected_before"])):
        if receipt["stage"] in {"prepared", "applied"}:
            rows = shared_memory._read_jsonl_checked(shared_memory._paths()["events"])
            after = all(wiki._source_fingerprint(page_id, rows) == value for page_id, value in batch["expected_after"].items())
            before = all(wiki._source_fingerprint(page_id, rows) == value for page_id, value in batch["expected_before"].items())
            if not after:
                if not before:
                    receipt = journal.transition(receipt["operation_id"], stage="stale-base", error_code="target_changed")
                else:
                    result = wiki.apply_source_batch(batch)
                    if result["status"] != "applied":
                        receipt = journal.transition(receipt["operation_id"], stage="stale-base", error_code="target_changed")
            if receipt["stage"] != "stale-base":
                receipt = journal.transition(receipt["operation_id"], stage="applied")
                rows = shared_memory._read_jsonl_checked(shared_memory._paths()["events"])
                if not all(wiki._source_fingerprint(page_id, rows) == value for page_id, value in batch["expected_after"].items()):
                    receipt = journal.transition(receipt["operation_id"], stage="stale-base", error_code="target_changed")
                else:
                    receipt = journal.transition(receipt["operation_id"], stage="committed")
        if receipt["stage"] == "committed":
            _record_integration(receipt, journal)
            receipt = _repair_effects(receipt, journal)
    return {"operation_id": receipt["operation_id"], "stage": receipt["stage"],
            "effects": {key: receipt[key] for key in ("artifacts", "search", "log")},
            "reason": receipt.get("error_code", ""),
            "newly_committed": receipt["stage"] == "committed" and not was_committed}


def recover_operation(receipt: dict, *, journal: Journal) -> dict:
    with journal.source_lock(receipt["revision_id"]):
        return _recover_locked(receipt, journal)


def _pending_source(source: dict, journal: Journal) -> bool:
    receipt = journal.load_source(source["revision_id"])
    if receipt is None or receipt["compile_state"] in {"pending", "failed"}:
        return True
    if receipt["compile_state"] == "skipped":
        return not set(source["observed_evidence_ids"]).issubset(
            receipt["evidence_by_role"].get(source["role"], []))
    state = receipt["routing"].get(source["role"], {})
    return (not state.get("integrated") or bool(state.get("unexamined"))
            or not set(source["observed_evidence_ids"]).issubset(state.get("integrated_evidence_ids", [])))


def _resume_compilations(journal: Journal, now: datetime, known: set[tuple[str, str]],
                         allowed_revision_ids: set[str] | None = None) -> tuple[list[dict], list[str]]:
    sources, reasons = [], []
    for path in sorted((journal.root / 'sources').glob('*.json')):
        if allowed_revision_ids is not None and path.stem not in allowed_revision_ids:
            continue
        receipt = journal.load_source(path.stem)
        if receipt['compile_state'] == 'skipped':
            continue
        roles = []
        for role, evidence in receipt['evidence_by_role'].items():
            if (receipt['revision_id'], role) in known:
                continue
            state = receipt['routing'].get(role, {})
            if (receipt['compile_state'] in {'pending', 'failed'} or not state.get('integrated')
                    or state.get('unexamined') or not set(evidence).issubset(state.get('integrated_evidence_ids', []))):
                roles.append((role, evidence))
        if not roles:
            continue
        saved = receipt['source']
        event = {'source': saved['provider'], 'url': saved['canonical_url'],
                 'raw_path': saved['raw_ref'], 'text_path': saved['text_ref'], 'manifest_path': saved['manifest_ref'],
                 'raw_sha256': saved['raw_sha256'], 'text_sha256': saved['text_sha256'],
                 'extraction_scope': saved['scope'], 'title': saved['title'],
                 'published_at': saved['published_at'], 'collected_at': saved['ingested_at']}
        if saved.get('content_kind'):
            event['content_kind'] = saved['content_kind']
        try:
            source = wiki_source_revision.build_revision(event, now=now, allow_chunking=True)
            if source['revision_id'] != receipt['revision_id']:
                raise ValueError('resume_revision_changed')
        except (ValueError, OSError, UnicodeError) as exc:
            reasons.append(exc.reason if isinstance(exc, wiki_source_revision.SourceGateError) else 'resume_source_unavailable')
            continue
        for role, evidence in roles:
            sources.append({**source, 'role': role, 'observed_evidence_ids': evidence})
    return sources, reasons


def _build_batch(source: dict, compiled: dict, state: dict, llm_fn: Callable) -> dict:
    targets = [hydrate_target(page_id) for page_id in state["selected"]]
    specs = state["new_page_specs"]
    live_specs = []
    for spec in specs:
        if wiki.get_page(spec["page_id"]):
            if not any(snap["page_id"] == spec["page_id"] for snap in targets):
                targets.append(hydrate_target(spec["page_id"]))
        else:
            live_specs.append(spec)
    known = [snap for snap in targets if any(claim.get("source_revision_id") == source["revision_id"] for claim in snap["claims"])]
    new = [snap for snap in targets if snap not in known]
    if new or live_specs:
        proposal = propose_integration(compiled, source, new, new_page_specs=live_specs, llm_fn=llm_fn)
    else:
        proposal = {"revision_id": source["revision_id"], "role": source["role"], "targets": []}
    proposal["targets"].extend({"page_id": snap["page_id"], "before_hash": snap["revision_hash"],
                                "additions": [], "relations": []} for snap in known)
    compiled_id = hashlib.sha256(("compiled-source:" + source["revision_id"]).encode()).hexdigest()
    existing = wiki.get_page(compiled_id)
    if existing:
        snapshot = hydrate_target(compiled_id)
        proposal["targets"].append({"page_id": compiled_id, "before_hash": snapshot["revision_hash"], "additions": [], "relations": []})
    else:
        first = compiled["claims"][0]
        spec = _new_spec({"title": compiled.get('title') or wiki._clean(source["title"], 80), "surface": state["surface"],
                          "kind": "note", "purpose": "compiled_source"}, source["revision_id"], state["surface"])
        proposal["targets"].append({**spec, "before_hash": None, "additions": [{"claim_ids": [first["claim_id"]],
            "as_of": first["as_of"], "scope": "Source-derived summary", "body": compiled["body"]}], "relations": []})
    batch = wiki.prepare_source_batch(proposal, compiled, source)
    # Cross-links are server-derived, never a model-selected page identity.
    for row in batch["upserts"]:
        if row["id"] in batch["target_ids"]:
            others = batch["target_ids"] if row["id"] == compiled_id else [compiled_id]
            row["links"] = wiki._clean_links([*others, *row.get("links", [])], self_id=row["id"])
    rows = shared_memory._read_jsonl_checked(shared_memory._paths()["events"])
    ids = set(batch["expected_after"])
    projected = [row for row in rows if row["id"] not in ids] + batch["upserts"]
    batch["expected_after"] = {page_id: wiki._source_fingerprint(page_id, projected) for page_id in ids}
    batch.update(role=source["role"], observed_evidence_ids=source["observed_evidence_ids"], routing_state=state)
    identity = {key: batch[key] for key in ("revision_id", "role", "expected_before", "expected_after")}
    batch["operation_id"] = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return batch


def run(*, events: list[dict] | None = None, dry_run: bool = True, llm_fn: Callable | None = None,
        source_limit: int = 5, target_limit: int = 20, now: datetime | None = None,
        allowed_revision_ids: set[str] | None = None) -> dict:
    if not 1 <= source_limit <= 100 or not 1 <= target_limit <= 20:
        raise ValueError("worker_limit_invalid")
    if not dry_run and llm_fn is None:
        if not _mode_enabled():
            raise ValueError("model_activation_required")
        from agent_console.agent import request_structured_output
        llm_fn = lambda prompt: request_structured_output(prompt, max_timeout=None)
    now = now or datetime.now(timezone.utc)
    events = source_collector.load_recent_events() if events is None else events
    records = shared_memory._read_jsonl_checked(shared_memory._paths()["events"])
    roles = {evidence_id: "conflict" for row in records for evidence_id in row.get("conflicting_evidence_ids", [])}
    discovery = wiki_source_revision.discover_revisions(events, now=now, evidence_roles=roles, allow_chunking=True)
    if allowed_revision_ids is not None:
        discovery['eligible'] = [source for source in discovery['eligible']
                                 if source['revision_id'] in allowed_revision_ids]
    result = {"dry_run": dry_run, "enabled": _mode_enabled(), "discovered": len(events),
              "eligible": len(discovery["eligible"]), "unavailable": len(discovery["unavailable"]),
              "pending_chunking": len(discovery["pending_chunking"]), "compiled": 0, "skipped": 0,
              "compile_failed": 0, "routing_pending": 0, "data_committed": 0, "effects_pending": 0,
              "stale_base": 0, "operation_ids": [], "reason_counts": dict(Counter(
                  item["reason"] for item in [*discovery["unavailable"], *discovery["pending_chunking"]]))}
    if dry_run:
        return result
    journal = Journal()
    known = {(item['revision_id'], item['role']) for item in discovery['eligible']}
    resumed, resume_reasons = _resume_compilations(journal, now, known, allowed_revision_ids)
    additional = [item for item in resumed if (item['revision_id'], item['role']) not in known]
    discovery['eligible'] = additional + discovery['eligible']
    result['eligible'] += len(additional)
    result['unavailable'] += len(resume_reasons)
    for reason in resume_reasons:
        result['reason_counts'][reason] = result['reason_counts'].get(reason, 0) + 1
    for item in [*discovery["unavailable"], *discovery["pending_chunking"]]:
        identity = item.get("discovery_id") or hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        safe_io.atomic_write_json(str(journal.root / "discovery" / (identity + ".json")), item)
    recovered = []
    for receipt in journal.pending_operations():
        if allowed_revision_ids is not None and receipt['revision_id'] not in allowed_revision_ids:
            continue
        recovered.append(recover_operation(receipt, journal=journal))
    chosen = [source for source in discovery["eligible"] if _pending_source(source, journal)][:source_limit]
    for source in chosen:
        with journal.source_lock(source["revision_id"]):
            if not _pending_source(source, journal):
                continue
            receipt = journal.record_attempt(source, state="pending")
            if receipt["compile_state"] == "skipped":
                continue
            if receipt["compile_state"] == "compiled":
                compiled = journal.load_compilation(receipt)
            else:
                try:
                    compiled = (compile_chunks(source, journal=journal, llm_fn=llm_fn)
                                if len(source["body"]) > CHUNK_CHARS
                                else compile_source(source, llm_fn=llm_fn))
                except ChunkingPending:
                    journal.record_attempt(source, state="pending", error_code="chunk_budget_pending")
                    result["pending_chunking"] += 1
                    result["reason_counts"]["chunk_budget_pending"] = result["reason_counts"].get("chunk_budget_pending", 0) + 1
                    continue
                except Exception as exc:
                    code = compilation_failure_code(exc)
                    journal.record_attempt(source, state="failed", error_code=code)
                    result["compile_failed"] += 1
                    result["reason_counts"][code] = result["reason_counts"].get(code, 0) + 1
                    continue
                receipt = journal.save_compilation(source, compiled)
                result["skipped" if compiled["skip_reason"] else "compiled"] += 1
                if compiled["skip_reason"]:
                    continue
            state = receipt["routing"].get(source["role"]) or discover_targets(compiled, surface="market")
            try:
                if state["unexamined"] or not state.get("examined") and not state.get("integrated"):
                    state = route_pass(compiled, state, llm_fn=llm_fn, limit=target_limit)
                    journal.save_routing(source["revision_id"], source["role"], state)
                batch = _build_batch(source, compiled, state, llm_fn)
                operation = journal.prepare(batch)
                recovered.append(_recover_locked(operation, journal))
                if state["unexamined"]:
                    result["routing_pending"] += 1
            except Exception:
                result["reason_counts"]["integration_failed"] = result["reason_counts"].get("integration_failed", 0) + 1
                result["routing_pending"] += 1
    result["operation_ids"] = list(dict.fromkeys(item["operation_id"] for item in recovered))
    result["data_committed"] = len({item["operation_id"] for item in recovered if item["newly_committed"]})
    result["stale_base"] = len({item["operation_id"] for item in recovered if item["stage"] == "stale-base"})
    result["effects_pending"] = len({item["operation_id"] for item in recovered
                                     if item["stage"] == "committed" and any(value != "done" for value in item["effects"].values())})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recover-only", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--source-limit", type=int, default=5)
    parser.add_argument("--target-limit", type=int, default=20)
    args = parser.parse_args()
    if args.status:
        pending = Journal().pending_operations()
        result = {"enabled": _mode_enabled(), "pending_operations": len(pending),
                  "stages": dict(Counter(receipt["stage"] for receipt in pending))}
    elif args.recover_only:
        if args.dry_run or not _mode_enabled():
            result = {"enabled": _mode_enabled(), "dry_run": True, "pending_operations": len(Journal().pending_operations())}
        else:
            journal = Journal()
            result = {"recovered": [recover_operation(receipt, journal=journal) for receipt in journal.pending_operations()]}
    else:
        result = run(dry_run=args.dry_run or not _mode_enabled(), source_limit=args.source_limit, target_limit=args.target_limit)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
