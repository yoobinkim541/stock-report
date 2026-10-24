# Split Relationship Inheritance Reproduction

Read-only code diagnosis plus disposable synthetic execution, not a live repair.
Prior goal continuation made progress by verifying runtime/graph blockers; this
continuation identifies a reproducible writer defect rather than repeating counts.

## Reproduction

`/tmp/wiki_structure_reproduce.py` patches page reads and batch writes to in-memory
synthetic dictionaries. Log/rebuild side effects are disabled. It uses the actual
`_merge_pages`, `_split_page` and publication relationship checker. No model,
private body, real source or canonical store is accessed.

- Valid graph before merge: target, source declaring child, child parent=source.
  After merging source into target: `child_parent_mismatch` for target -> child.
  `_merge_pages` unions source structural tags into target but does not reparent
  the child. The source archive and child themselves retain the prior relation.
- Valid graph before split: source is a child of parent. Splitting source creates
  a child inheriting `split_from:parent` and adding `split_from:source`, with the
  parent field set to source. Checker reports `conflicting_parents` and the new
  source -> child `child_parent_mismatch`.

Luna independently identified the same tag-copy paths using code inspection.
Neither reproduction establishes the historical writer of the eight live errors.

## Proposed Bounded Correction

When merging, union ordinary tags but retain only the target's own structural
split tags; do not acquire child ownership or ancestry from merged sources.
Keep archived source relationship metadata and all source bodies intact. When
creating manual split children, filter inherited structural split tags before
adding their direct parent; retain genuine existing source/child relationships.
Preserve reserved full-length relationship identities through normalization.

Convert both synthetic executions into RED/GREEN regressions, then test nested
split/merge, unchanged bodies/provenance, long IDs and publication validation.
No automatic repair of the eight operational records, reparenting, deletion,
receipt rewrite or generation activation is authorized by this proposal. The
user has been asked to approve this bounded future-write correction separately
from the pending immutable-source-note architecture.
