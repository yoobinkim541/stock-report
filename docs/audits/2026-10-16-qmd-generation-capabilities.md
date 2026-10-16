# QMD Generation Isolation Capability Audit

Date: 2026-10-16

## Result

**Supported for isolated lexical indexing and search on the installed QMD
2.5.3.** The previous audit result was incorrect: it stopped at generic
top-level help even though the installed package documents the collection
recipe and supports named indexes.

The real probe returned `supported: true`, an empty reason, and a verified
recipe. It built one collection in each of two distinct named indexes, then
searched each index for a shared synthetic term. Both results matched that
index's exact synthetic file and sentinel; the B query did not return A's
identity.

## Local QMD Evidence

Package root: `/home/ubuntu/.hermes/node/lib/node_modules/@tobilu/qmd`

- `README.md` documents `qmd collection add <path> --name <name>` in Collection
  Management, `qmd --index work search ...` in Examples, and XDG cache
  configuration under Environment Variables.
- `dist/cli/qmd.js:2498` selects the explicit named index before project-local
  config discovery. Thus every invocation can bypass ancestor `.qmd` config by
  passing `--index`.
- `dist/collections.js:58` resolves collection YAML below
  `XDG_CONFIG_HOME/qmd`; `dist/store.js:418` resolves the named SQLite index
  below `XDG_CACHE_HOME/qmd`.
- `dist/cli/qmd.js:1456` shows `collection add` persists the synthetic
  collection and indexes files via `indexFiles`; it does not invoke embedding.
  It only reports that `qmd embed` could be run separately.
- `dist/cli/qmd.js:2219` implements `search` through `searchFTS`, the lexical
  search path. The probe does not call `embed`, `vsearch`, or hybrid `query`.

## Isolation Checks

Before any collection mutation, the probe creates fresh A/B notes and isolated
HOME, XDG config, XDG cache, and temporary directories. It computes each
expected config path (`config/qmd/<index>.yml`) and DB path
(`cache/qmd/<index>.sqlite`), confirms both resolve inside the temporary root
and do not already exist, then invokes explicit-index `--version`, `--help`,
and `collection help` calls. For each index, the help-reported effective DB
path must exactly equal the preflighted path below the isolated cache. Any
missing index option, unexpected path, nonzero exit, or timeout stops before
collection creation.

All subsequent commands also include the explicit named index. The process
environment is allowlisted (PATH, locale/color controls, isolated HOME/XDG and
TMP roots); inherited index/config overrides and credentials are not passed.
Only A/B synthetic collection directories are supplied to `collection add`.
No live index/store, network, model, or download operation is used.

Verified command templates:

```text
{executable} --index {index_name} collection add {collection_path} --name {collection_name}
{executable} --index {index_name} search {query} --format json --full-path --full -n 10 -c {collection_name}
```

The probe performs the build and query against both `qmd_probe_a` / `probe-a`
and `qmd_probe_b` / `probe-b`. Relative `./a/...` and `./b/...` paths are
resolved against the isolated QMD working directory before checking identity.

## Verification

- TDD RED: the first expanded test run failed because invocations did not yet
  use explicit named indexes.
- TDD RED: a test matching QMD's real relative full-path output failed identity
  checking until results were resolved against the isolated working directory.
- TDD RED: an outside-cache path advertised by help was initially accepted;
  the new pre-mutation path gate now rejects it before any `collection add`.
- GREEN: `.venv/bin/python -m pytest -q tests/test_qmd_generation_capabilities.py`
  passed: 5 tests, covering successful build/query, B returning A, an unconfined
  index path, timeout, and nonzero exit.
- Real local probe: `.venv/bin/python scripts/probe_qmd_generation.py` exited
  0 and returned `supported: true` with the recipe above.
- A separate isolated reproduction showed the actual result payloads contained
  `/workspace/a/probe-a.md` with `sentinel-a` and
  `/workspace/b/probe-b.md` with `sentinel-b`. QMD printed its “Run qmd embed”
  advisory only; no embed command was executed.

## Scope

This confirms only that this installed QMD version can isolate lexical index
build/query operations. It does not activate production search, create a live
generation, or establish Task 4 search integration readiness by itself.
