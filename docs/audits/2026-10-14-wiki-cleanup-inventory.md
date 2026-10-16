# Wiki Cleanup Inventory And Protection

## Read-Only Live Inventory

4,006 records: risk 1,280; note 22; concept 209; source_digest 1,674; playbook 821. States: draft 2,794; reviewed 329; archived 883. Structured source knowledge/claims occur in 23 records. No raw record has both empty body and summary. There are 39 child pages, four displayed bodies below 200 characters, and two identical-body duplicate pairs.

The duplicate pairs each contain one draft and one archived concept, identical source references and structured knowledge, with existing links. They are not evidence of two discardable independent drafts. No deletion or rewrite was made.

## Defect And Repair

Age cleanup protected semantic kinds and provenance links but omitted source_knowledge on note pages. Eight RED cases demonstrated that stale structured-only notes could be archived or deleted despite claims, limitations, relations, or integration provenance. Nonempty structured knowledge now protects the page regardless of kind and age.

Expanded tests verify both preview and applying paths cannot call deletion, upsert, or artifact rebuild for protected pages: 16 passed. Related health and agent suite: 213 passed. Diff whitespace validation passed.

Sol review identified two additional automatic deletion paths: health-review actions and chat curation. Separate RED regressions reproduced unconditional health deletion and successful chat deletion of structured-only knowledge. Both now reject deletion of structured knowledge; health actions also retain source-backed documents. Chat merge guards use the same protection because merging through the conversation-only path cannot safely replace source-compiled knowledge. Explicit user deletion API is not changed by this automatic-maintenance repair.

Final combined protection/health/agent regressions: 223 passed. The temporary initial chat test returned None because its dialogue did not satisfy the existing auto-curation gate; the fixture was corrected before treating the actual delete-result failure as RED evidence.

## Operational Preview

After repair, all 4,006 pages were checked using dry_run: archive 0, delete 0, stale skipped 200. This is a verified no-op cleanup result, not overall wiki completion. Reviewed labels alone are not independent fact verification; the new source-compiled rollout still retains draft claims. Broader semantic synthesis, source recovery and query/fileback gates remain separate.
