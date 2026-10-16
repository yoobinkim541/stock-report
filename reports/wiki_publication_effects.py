"""Generation-bound effects without changing source/topic receipt protocols."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import safe_io
from agent_console import qmd_generation, shared_memory, wiki, wiki_publication as publication
from agent_console import wiki_publication_config
from reports.wiki_source_journal import _identity, _encoded


def publication_root() -> Path:
    return wiki_publication_config.publication_root(wiki.wiki_artifacts_dir())


def search_dependencies():
    """Only an explicitly installed, previously probed recipe enables indexing."""
    path = os.environ.get('AGENT_CONSOLE_WIKI_QMD_CAPABILITY_FILE', '').strip()
    if not path:
        return {'supported': False, 'reason': 'capability_not_configured', 'recipe': None}, None
    capability = json.loads(publication._read_regular(Path(path)))
    return capability, subprocess.run


def _digest(value) -> str:
    return hashlib.sha256(_encoded(value).encode('utf-8')).hexdigest()


def _receipt(operation_id, journal):
    source = journal._read(journal._path('operations', operation_id))
    topic = journal.load_topic_operation(operation_id)
    if (source is None) == (topic is None):
        raise ValueError('publication_operation_ambiguous')
    receipt = source or topic
    if receipt['operation_id'] != operation_id or receipt['stage'] != 'committed':
        raise ValueError('publication_not_committed')
    return receipt


def _binding(receipt, journal):
    batch = receipt['batch']
    revisions = batch['revision_ids'] if receipt['schema_version'] == 2 else [receipt['revision_id']]
    sources = {}
    for revision in revisions:
        source = journal.load_source(revision)
        if not source or source['compile_state'] != 'compiled':
            raise ValueError('publication_source_unavailable')
        sources[revision] = {key: source[key] for key in (
            'revision_id', 'source', 'compile_sha256', 'compile_ref')}
    return _digest({'schema_version': receipt['schema_version'], 'batch': batch, 'sources': sources})


def _check_commit(receipt, rows, *, historical=False):
    if receipt['schema_version'] == 2:
        from reports.wiki_topic_transaction import _commit_record
        witness = _commit_record(receipt['batch'])
        if [r for r in rows if r['id'] == witness['id']] != [witness]:
            raise ValueError('topic_commit_witness_mismatch')
    if not historical and any(wiki._source_fingerprint(pid, rows) != digest
                             for pid, digest in receipt['batch']['expected_after'].items()):
        raise ValueError('publication_target_mismatch')


def _identity_for(snapshot):
    files = publication.render_candidate(snapshot)
    if not publication.validate_candidate(files)['ok']:
        raise ValueError('invalid_publication_candidate')
    manifest = {'version': 1, 'snapshot_sha256': snapshot['snapshot_sha256'],
                'schema_sha256': snapshot['schema_sha256'],
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}}
    digest = _digest(manifest)
    return {'generation_id': digest, 'manifest_sha256': digest,
            'snapshot_sha256': snapshot['snapshot_sha256']}


def _save(path, sidecar):
    publication._no_symlinks(path)
    safe_io.atomic_write_json(str(path), sidecar)
    publication._fsync_directory(path.parent)
    publication._fsync_directory(path.parent.parent)


def _verify_generation(root, identity):
    reader = publication.pin_generation(root=root, generation_id=identity['generation_id'])
    if any(getattr(reader, key) != value for key, value in identity.items()):
        raise ValueError('publication_identity_mismatch')


def _pointer(root):
    path = publication._no_symlinks(root / 'current.json')
    return json.loads(publication._read_regular(path)) if path.exists() else None


def _activation_proof(sidecar, root, operation_id, *, record=False, missing_ok=False):
    path = publication._no_symlinks(root / 'publication-activations' / (operation_id + '.json'))
    expected = {'operation_id': operation_id, 'receipt_sha256': sidecar['receipt_sha256'],
                'publication': sidecar['publication']}
    if path.exists():
        if json.loads(publication._read_regular(path)) != expected:
            raise ValueError('publication_activation_proof_mismatch')
    elif record:
        # Called only under the publication lock after verifying the active
        # pointer. This separate witness means editing a sidecar status bit is
        # not evidence of activation, including for retained older generations.
        if _pointer(root) != {**sidecar['publication'], 'operation_id': operation_id}:
            raise ValueError('publication_not_activated')
        _save(path, expected)
    elif missing_ok:
        return False
    else:
        raise ValueError('publication_activation_proof_missing')
    return True


def _publish_intent(sidecar, *, root, operation_id):
    # The public publisher unconditionally activates. Build in a private root,
    # then install and compare-and-activate under the SAME Task 3 lock. Never
    # reacquire a store lock or call the locking public activator inside it.
    candidate = root / '.effect-candidates' / operation_id
    identity = publication.publish_snapshot(sidecar['snapshot'], root=candidate, operation_id=operation_id)
    if identity != sidecar['publication']:
        raise ValueError('publication_identity_mismatch')
    with publication._publication_lock(root) as locked_root:
        if _pointer(locked_root) != sidecar['predecessor']:
            return False
        destination = locked_root / 'generations' / identity['generation_id']
        if not destination.exists():
            os.rename(candidate / 'generations' / identity['generation_id'], destination)
            publication._fsync_directory(candidate / 'generations')
            publication._fsync_directory(locked_root / 'generations')
        _verify_generation(locked_root, identity)
        publication._activate_locked(identity['generation_id'], locked_root, operation_id)
        _activation_proof(sidecar, locked_root, operation_id, record=True)
    return True


def repair_publication(operation_id: str, *, journal, now=None) -> dict:
    """Repair frozen effects only; no compiler, model or quota interfaces are used.

    Source/target locks may be held by callers. Capture/commit checks release the
    store lock before publication or search locks. The sidecar lock serializes
    same-operation repairs; the effects lock orders different operations.
    """
    _identity(operation_id)
    root = publication._no_symlinks(publication_root())
    path = journal._path('publication-effects', operation_id)
    publication._no_symlinks(path)
    result = {'generation_id': None, 'artifacts': 'failed', 'search': 'failed', 'log': 'failed'}
    with safe_io.file_write_lock(str(journal.root / 'publication-effects-order')):
        with safe_io.file_write_lock(str(path)):
            publication._no_symlinks(path)
            receipt = _receipt(operation_id, journal)
            binding = _binding(receipt, journal)
            with shared_memory._events_lock():
                rows = shared_memory._read_jsonl_checked(shared_memory._paths()['events'])
                _check_commit(receipt, rows, historical=path.exists())
            if path.exists():
                sidecar = json.loads(path.read_bytes())
                if (sidecar.get('version') != 1 or sidecar.get('operation_id') != operation_id
                        or sidecar.get('receipt_sha256') != binding
                        or sidecar.get('root') != str(root)):
                    raise ValueError('publication_sidecar_mismatch')
                snapshot = sidecar['snapshot']
                # Old canonical targets may have advanced, but frozen targets must
                # still prove this operation's exact committed after-images.
                if any(wiki._source_fingerprint(pid, snapshot['records']) != digest
                       for pid, digest in receipt['batch']['expected_after'].items()):
                    raise ValueError('publication_snapshot_target_mismatch')
                if sidecar['publication'] != _identity_for(snapshot):
                    raise ValueError('publication_sidecar_identity_mismatch')
                descriptor = sidecar.get('descriptor')
                if descriptor is not None and (
                        _digest(descriptor) != sidecar.get('descriptor_sha256')
                        or any(descriptor.get(k) != v for k, v in sidecar['publication'].items())
                        or descriptor.get('root') != str(root)):
                    raise ValueError('publication_descriptor_mismatch')
            else:
                # Prior legacy success is not evidence of a generation. Do not
                # reinterpret it or manufacture migration proof on replay.
                if any(receipt[k] == 'done' for k in ('artifacts', 'search', 'log')):
                    return result
                # Bracket capture with publication-locked pointer observations,
                # never holding that lock while capture acquires STORE. A newer
                # activation during capture must not become this intent's base.
                with publication._publication_lock(root) as locked_root:
                    predecessor = _pointer(locked_root)
                snapshot = publication.capture_snapshot()
                if any(wiki._source_fingerprint(pid, snapshot['records']) != digest
                       for pid, digest in receipt['batch']['expected_after'].items()):
                    raise ValueError('publication_snapshot_target_mismatch')
                sidecar = {'version': 1, 'operation_id': operation_id, 'receipt_sha256': binding,
                           'root': str(root), 'snapshot': snapshot, 'publication': _identity_for(snapshot),
                           'predecessor': predecessor, 'activated': False,
                           'descriptor': None, 'descriptor_sha256': None}
                _save(path, sidecar)
            identity = sidecar['publication']
            result['generation_id'] = identity['generation_id']
            try:
                retained = root / 'generations' / identity['generation_id']
                if retained.exists():
                    _verify_generation(root, identity)
                    # A durable witness survives failure to save activated=True,
                    # even if another generation has since become current.
                    if not sidecar['activated']:
                        with publication._publication_lock(root) as locked_root:
                            if not _activation_proof(sidecar, locked_root, operation_id, missing_ok=True):
                                pointer = _pointer(locked_root)
                                if pointer != {**identity, 'operation_id': operation_id}:
                                    if pointer != sidecar['predecessor']:
                                        return result
                                    # Rename completed but pointer replacement
                                    # did not. Resume only against the old base.
                                    _check_commit(receipt, rows)
                                    publication._activate_locked(identity['generation_id'], locked_root, operation_id)
                                _activation_proof(sidecar, locked_root, operation_id, record=True)
                else:
                    if sidecar['activated']:
                        raise ValueError('activated_generation_missing')
                    # A previously frozen intent must not become an implicit
                    # rollback after canonical targets have advanced.
                    _check_commit(receipt, rows)
                    if not _publish_intent(sidecar, root=root, operation_id=operation_id):
                        return result
                _verify_generation(root, identity)
                _activation_proof(sidecar, root, operation_id)
                sidecar['activated'] = True
                _save(path, sidecar)
                result['artifacts'] = 'done'
            except (OSError, ValueError, KeyError):
                return result
            try:
                current = publication.pin_generation(root=root)
                if current.generation_id == identity['generation_id']:
                    health = qmd_generation.generation_health(root=root)
                    descriptor = sidecar['descriptor']
                    if (health.get('status') == 'ready' and health.get('indexed') == identity
                            and descriptor is not None
                            and json.loads(publication._read_regular(root / 'search-current.json')) == descriptor):
                        result['search'] = 'done'
                    else:
                        capability, runner = search_dependencies()
                        built = qmd_generation.build_index(identity, root=root, capability=capability, runner=runner)
                        health = qmd_generation.generation_health(root=root)
                        descriptor = built.get('descriptor')
                        if (built.get('status') != 'ready' or not descriptor
                                or built.get('generation_id') != identity['generation_id']
                                or built.get('snapshot_sha256') != identity['snapshot_sha256']
                                or health.get('status') != 'ready' or health.get('indexed') != identity
                                or any(descriptor.get(k) != v for k, v in identity.items())
                                or descriptor.get('root') != str(root)
                                or json.loads(publication._read_regular(root / 'search-current.json')) != descriptor):
                            raise ValueError('publication_search_pending')
                        sidecar['descriptor'] = descriptor
                        sidecar['descriptor_sha256'] = _digest(descriptor)
                        _save(path, sidecar)
                        result['search'] = 'done'
                elif sidecar['descriptor'] is not None:
                    if _digest(sidecar['descriptor']) != sidecar['descriptor_sha256']:
                        raise ValueError('publication_descriptor_mismatch')
                    # Health cannot attest an old, non-active index. Never switch
                    # the descriptor backwards just to complete an old receipt.
            except (OSError, ValueError, KeyError, TypeError):
                pass
            try:
                wiki._append_wiki_log('publication', operation_id=operation_id, **identity,
                    page_ids=receipt['batch']['target_ids'])
                result['log'] = 'done'
            except (OSError, ValueError):
                pass
            return result


def dispatch_effects(receipt, journal):
    """Project verified sidecar state onto the unchanged legacy effect fields."""
    result = repair_publication(receipt['operation_id'], journal=journal)
    # A legacy done receipt without a sidecar remains legacy, not generation proof.
    if result['generation_id'] is None:
        return receipt
    effects = {key: result[key] for key in ('artifacts', 'search', 'log')}
    transition = journal.transition_topic if receipt['schema_version'] == 2 else journal.transition
    return transition(receipt['operation_id'], effects=effects,
                      error_code='' if all(v == 'done' for v in effects.values()) else 'publication_effects_pending')
