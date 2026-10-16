# Topic Bootstrap Acceptance

## Implemented Boundaries

The approved specification and five-task plan are in the corresponding
2026-10-15 public-topic-bootstrap spec/plan files. Code is default-off behind
AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED. No production setting was changed.

Synthetic acceptance now exercises the actual bounded runner with injected JSON
provider responses and temporary real stores: two public originals seed a topic,
a third updates the same topic, old prose/claims/limitations remain, all three
source URLs survive Markdown, source notes remain unchanged, and replay performs
no additional model call or data change. This is not actual-model semantic proof.

A separate committed long-topic acceptance test verifies lossless child splitting,
retained source-revision and claim IDs, limitations, Markdown tail content,
1,600-character chat excerpt cap and real Streamlit AppTest child navigation.
It is widget-layer verification, not a production browser/deployment check.

## Review And TDD Repairs

- Notes-only model projection excludes omitted private IDs and dependency hashes.
- Current source and compilation are rebound to retained archive/journal evidence
  before exposure; invalid inputs invoke no model.
- Frozen writes are rebuilt from the validated proposal before commit; a
  recalculated receipt checksum is not authentication. An atomic store witness
  supports legitimate crash and later-append recovery without trusting markers.
- Runner topic failures have explicit enrollment/pending state before legacy
  work begins. Enrolled failures resume beyond the recent feed, through fresh
  quota reservations; historical completed sources without enrollment stay done.
- Dry/apply use the same revision-and-role keys, retaining pending conflict work
  beside a completed support role.

Each repair had a reproduced failing synthetic test followed by passing tests.
Task 1–3 independent reviews and final scoped reviews are in the plan ledger.
Task 4 initially lacked review because a requested worker hit the account usage
limit. Local TDD continued; after the reported reset time one same-model review
request succeeded and found the retry defects above. Exposed tools do not attest
the reviewer backend identity, so the record states the requested model only.

## Verification Register

- Before the final retry repairs: selected wiki 1,001 passed, one skip, 102.80s.
- Final retry/runner/topic/budget subset: 41 passed, 2.03s.
- Reviewer scoped verification: 23 passed, including dry/apply role collision.
- Latest full-repository verification: 4,431 passed, two skipped, 41 warnings,
  exit 0 in 854.19 seconds. Approved loopback-listener permissions were used;
  no code edits occurred during the run. Skips retain the existing opt-in canvas
  performance and inapplicable legacy-selector exclusions; warnings concern
  scientific feature names, plotting and deprecations, not a warning-free run.
- Independent final review's role-collision repair was approved after 23 scoped
  tests. No additional code changes followed that approval before the full run.

## Not Yet Proved

Actual-model multi-source topic semantics and subsequent live updates are not
certified. Public daily reservations, private-source restrictions and separate
pilot approval remain binding. SaveTicker human authentication recovery, latest
listing activation, multi-file artifact publication consistency and production
browser verification remain separate incomplete gates. No deployment, shared
branch merge, destructive cleanup or full-goal completion is claimed.
