# Public Original Integrity

## Read-Only Evidence

All 35 compiled source receipts in the approved public journal were inspected.
No original, receipt, wiki record or quota file was written and no model or
provider was called.

- Original bytes matched the receipt raw SHA-256: 35/35.
- Extracted UTF-8 bytes matched the receipt text SHA-256: 35/35.
- Compiled artifacts passed the journal hash/revision checks: 35/35.
- Manifest raw hash, canonical URL and raw/text/manifest paths matched receipt
  identity: 35/35.
- Manifest-level extracted-text SHA-256 was absent: 35/35. Text integrity was
  checked against the compiled source receipt, not an independently stored
  manifest text hash. Existing immutable manifests were not rewritten.

The initial naive filesystem probe could not find compressed members. The
archive-aware `raw_archive.read_raw_artifact/read_raw_text` API recovered them;
this was not missing original data. A subsequent deliberately stricter manifest
check failed because it required the absent optional text-hash field; inspection
separated that absence from a hash mismatch. The final manifest identity check
passed without weakening original or receipt byte-hash checks.

These checks establish retained-byte and recorded-identity consistency only.
They do not independently certify extraction accuracy, claims, interpretation,
source reliability or complete processing of the pending public backlog.
