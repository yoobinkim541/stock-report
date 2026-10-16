"""Acceptance audit of the live wiki against the Karpathy "LLM Wiki" pattern.

Each check is a pure function over explicit inputs (pages, artifacts, journals,
health), so the same code is exercised by synthetic tests and by the live gate.
The audit is read-only: it never writes to the store, artifacts or journals.
Passing it is structural evidence, not a certificate that every claim is true.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

RAW_LAYER_KINDS = frozenset({"source_index"})
RAW_LAYER_TAG = "raw_index"
LONG_PAGE_LIMIT = 6000
MIN_TOPIC_PAGES = 30
MIN_COMPOUNDED_PAGES = 20
LOG_OPS = ("ingest", "query", "lint", "split", "archive", "merge", "maintenance")
LOG_HEADER = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] (" + "|".join(LOG_OPS) + r") \| .+$")
SCHEMA_SECTIONS = ("layers", "ingest", "query", "lint", "index.md", "log.md")
FRONTMATTER_KEYS = ("id", "kind", "status", "tags", "updated", "source_count")
_LINK_TEXT = r"(?:[^\[\]|#]|\[[^\[\]]*\])+"
WIKILINK = re.compile(r"\[\[(" + _LINK_TEXT + r")(?:#[^\]|]*)?(?:\|(?:[^\[\]]|\[[^\[\]]*\])*)?\]\]")
URL = re.compile(r"https?://\S+")

_MECHANICAL_MARKERS = (
    re.compile(r"수집 이벤트 \d+건"),
    re.compile(r"이 카드는 수집된 원문과 다이제스트를"),
    re.compile(r"^수집 소스 위키:"),
)


@dataclass
class AuditInputs:
    pages: list[dict]
    index_md: str | None = None
    log_md: str | None = None
    log_baseline: dict | None = None
    schema_export: str | None = None
    schema_source: str | None = None
    markdown: dict[str, str] = field(default_factory=dict)
    compiled_sources: dict[str, dict] = field(default_factory=dict)
    read_artifact: Callable[[str], bytes | None] = lambda _path: None
    collection_health: dict[str, dict] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def file_stem(page_id: str) -> str:
    from agent_console import qmd_search

    return qmd_search._safe_file_stem(qmd_search._clean(page_id, 100))


def _active(pages: list[dict]) -> list[dict]:
    return [p for p in pages if isinstance(p, dict) and p.get("id") and p.get("status") != "archived"]


def is_raw_layer(page: dict) -> bool:
    return page.get("kind") in RAW_LAYER_KINDS or RAW_LAYER_TAG in (page.get("tags") or [])


def wiki_layer(pages: list[dict]) -> list[dict]:
    return [p for p in _active(pages) if not is_raw_layer(p)]


def _claims(page: dict) -> list[dict]:
    knowledge = page.get("source_knowledge")
    claims = knowledge.get("claims") if isinstance(knowledge, dict) else None
    return [c for c in claims or [] if isinstance(c, dict) and str(c.get("text") or "").strip()]


def _revisions(page: dict) -> set[str]:
    return {str(c.get("source_revision_id")) for c in _claims(page) if c.get("source_revision_id")}


def _has_source_ref(page: dict) -> bool:
    from agent_console import wiki

    return bool(_revisions(page)) or wiki.has_non_conversation_source_refs(page.get("source_refs") or [])


def mechanical_reasons(page: dict) -> list[str]:
    """Why a page is a catalog/template instead of synthesized knowledge."""
    title = str(page.get("title") or "")
    body = str(page.get("body") or "")
    reasons = []
    for marker in _MECHANICAL_MARKERS:
        if marker.search(title) or marker.search(body):
            reasons.append("template_marker")
            break
    lines = [line.strip() for line in body.splitlines() if len(line.strip()) > 20]
    if lines:
        repeat = Counter(lines).most_common(1)[0][1]
        duplicates = len(lines) - len(set(lines))
        if repeat >= 3 and duplicates / len(lines) >= 0.3:
            reasons.append("repeated_lines")
        url_lines = sum(1 for line in lines if URL.search(line))
        prose = URL.sub("", body)
        if url_lines / len(lines) > 0.5 and len(re.sub(r"\s+", "", prose)) < 200:
            reasons.append("link_dominant")
    navigational = page.get("kind") == "overview" and WIKILINK.search(body)
    if len(body.strip()) < 80 and not _claims(page) and not navigational:
        reasons.append("empty_or_stub")
    return reasons


def _result(ok: bool, metric: Any, threshold: Any, evidence: Any = None) -> dict:
    return {"pass": bool(ok), "metric": metric, "threshold": threshold, "evidence": evidence or {}}


def _sha(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def check_raw_immutable(inp: AuditInputs) -> dict:
    cited = set().union(*(_revisions(p) for p in wiki_layer(inp.pages))) if inp.pages else set()
    missing_receipt, unavailable, mismatched, verified = [], [], [], 0
    for revision in sorted(cited):
        source = inp.compiled_sources.get(revision)
        if not source:
            missing_receipt.append(revision)
            continue
        for ref, digest in (("raw_ref", "raw_sha256"), ("text_ref", "text_sha256")):
            actual = _sha(inp.read_artifact(str(source.get(ref) or "")))
            if actual is None:
                unavailable.append(revision)
            elif actual != source.get(digest):
                mismatched.append(revision)
        verified += 1
    ok = bool(cited) and not (missing_receipt or unavailable or mismatched)
    return _result(ok, {"cited": len(cited), "verified": verified}, "all cited sources hash-verified",
                   {"missing_receipt": missing_receipt[:20], "unavailable": sorted(set(unavailable))[:20],
                    "mismatched": sorted(set(mismatched))[:20]})


def check_knowledge_not_links(inp: AuditInputs) -> dict:
    pages = wiki_layer(inp.pages)
    mechanical = {p["id"]: mechanical_reasons(p) for p in pages}
    mechanical = {k: v for k, v in mechanical.items() if v}
    # Overview hubs are navigation (Wikipedia portals): their evidence is the pages they list.
    unsourced = [p["id"] for p in pages if p["id"] not in mechanical and not _has_source_ref(p)
                 and not (p.get("kind") == "overview" and (p.get("links") or WIKILINK.search(str(p.get("body") or ""))))]
    ok = bool(pages) and not mechanical and not unsourced
    reasons = Counter(r for v in mechanical.values() for r in v)
    return _result(ok, {"wiki_pages": len(pages), "mechanical": len(mechanical), "unsourced": len(unsourced)},
                   "0 mechanical, 0 unsourced", {"reasons": dict(reasons), "mechanical_ids": list(mechanical)[:20],
                                                 "unsourced_ids": unsourced[:20]})


def _summary_pages(pages: list[dict]) -> dict[str, list[dict]]:
    by_revision: dict[str, list[dict]] = {}
    for page in wiki_layer(pages):
        if "compiled_source" in (page.get("tags") or []):
            for revision in _revisions(page):
                by_revision.setdefault(revision, []).append(page)
    return by_revision


def check_source_summaries(inp: AuditInputs) -> dict:
    compiled = {r for r, s in inp.compiled_sources.items() if s.get("compile_state", "compiled") == "compiled"}
    summaries = _summary_pages(inp.pages)
    missing = sorted(compiled - set(summaries))
    return _result(bool(compiled) and not missing, {"compiled": len(compiled), "with_summary": len(compiled) - len(missing)},
                   "every compiled source has a summary page", {"missing": missing[:20]})


def _topic_pages(pages: list[dict]) -> list[dict]:
    return [p for p in wiki_layer(pages) if "compiled_source" not in (p.get("tags") or [])
            and not p.get("parent_page_id") and not mechanical_reasons(p)]


def check_entity_topic_pages(inp: AuditInputs) -> dict:
    summary_ids = {p["id"] for rows in _summary_pages(inp.pages).values() for p in rows}
    qualified = [p["id"] for p in _topic_pages(inp.pages) if _claims(p)
                 and summary_ids & {*(p.get("links") or []), *(p.get("backlinks") or [])}]
    return _result(len(qualified) >= MIN_TOPIC_PAGES, len(qualified), f">= {MIN_TOPIC_PAGES}",
                   {"sample": qualified[:10]})


def check_compounding(inp: AuditInputs) -> dict:
    pages = [p for p in wiki_layer(inp.pages) if not p.get("parent_page_id")]
    compounded = [p["id"] for p in pages if len(_revisions(p)) >= 2]
    malformed, flagged = [], 0
    for page in pages:
        claims = {c.get("claim_id"): c for c in _claims(page)}
        for claim in claims.values():
            for other in claim.get("competing_claim_ids") or []:
                flagged += 1
                rival = claims.get(other)
                if not rival or not claim.get("as_of") or not rival.get("as_of") \
                        or claim.get("source_revision_id") == rival.get("source_revision_id"):
                    malformed.append(page["id"])
    ok = len(compounded) >= MIN_COMPOUNDED_PAGES and not malformed
    return _result(ok, {"compounded_pages": len(compounded), "flagged_contradictions": flagged},
                   f">= {MIN_COMPOUNDED_PAGES} multi-source pages; contradictions well-formed",
                   {"sample": compounded[:10], "malformed_contradictions": sorted(set(malformed))[:10]})


def parse_index(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Return {stem: [entry lines]} for non-archived sections and section headings."""
    entries: dict[str, list[str]] = {}
    headings, archived = [], False
    for line in text.splitlines():
        if line.startswith("## ") or line.startswith("<summary>## "):
            heading = re.sub(r"</?summary>", "", line)[3:].strip()
            archived = heading.lower().startswith("archived")
            if not archived:
                headings.append(heading)
            continue
        match = WIKILINK.search(line) if line.startswith("- ") else None
        if match and not archived:
            entries.setdefault(match.group(1).strip(), []).append(line)
    return entries, headings


def check_index_md(inp: AuditInputs) -> dict:
    if not inp.index_md:
        return _result(False, None, "index.md present", {"error": "missing"})
    entries, headings = parse_index(inp.index_md)
    expected = {file_stem(p["id"]) for p in wiki_layer(inp.pages)}
    missing = sorted(expected - set(entries))
    extra = sorted(set(entries) - expected)
    duplicated = sorted(s for s, rows in entries.items() if len(rows) > 1)
    bad_entries = [s for s, rows in entries.items() if s in expected and not all(
        re.search(r" — \S", row) and re.search(r"\d{4}-\d{2}-\d{2}", row) and re.search(r"sources: \d+", row)
        for row in rows)]
    ok = not (missing or extra or duplicated or bad_entries) and len(headings) >= 2
    return _result(ok, {"expected": len(expected), "listed": len(entries), "headings": len(headings)},
                   "every active page once, >=2 category headings, summary+date+sources",
                   {"missing": missing[:10], "extra": extra[:10], "duplicated": duplicated[:10],
                    "bad_entries": bad_entries[:10], "headings": headings[:20]})


def _log_entries(text: str) -> list[tuple[str, str]]:
    entries, header, body = [], None, []
    for line in text.splitlines():
        if line.startswith("## ["):
            if header is not None:
                entries.append((header, "\n".join(body)))
            header, body = line, []
        elif header is not None:
            body.append(line)
    if header is not None:
        entries.append((header, "\n".join(body)))
    return entries


def check_log_md(inp: AuditInputs) -> dict:
    if not inp.log_md:
        return _result(False, None, "log.md present", {"error": "missing"})
    data = inp.log_md.encode("utf-8")
    appended_only = True
    if inp.log_baseline:
        size = int(inp.log_baseline.get("size") or 0)
        appended_only = len(data) >= size and _sha(data[:size]) == inp.log_baseline.get("sha256")
    entries = _log_entries(inp.log_md)
    bad = [h for h, _ in entries if not LOG_HEADER.match(h)]
    ingest_text = "\n".join(h + "\n" + b for h, b in entries if LOG_HEADER.match(h) and " ingest | " in h)
    compiled = [r for r, s in inp.compiled_sources.items() if s.get("compile_state", "compiled") == "compiled"]
    unlogged = [r for r in compiled if r not in ingest_text]
    ok = bool(entries) and appended_only and not bad and not unlogged
    return _result(ok, {"entries": len(entries), "bad_headers": len(bad), "unlogged_ingests": len(unlogged)},
                   "append-only; all headers '## [YYYY-MM-DD] op | title'; ingest per compiled source",
                   {"append_only": appended_only, "bad_sample": bad[:5], "unlogged": unlogged[:10]})


def check_schema(inp: AuditInputs) -> dict:
    source = inp.schema_source or ""
    identical = bool(source) and inp.schema_export == source
    lower = source.lower()
    missing = [s for s in SCHEMA_SECTIONS if s not in lower]
    return _result(identical and not missing, {"identical": identical, "missing_sections": missing},
                   "exported schema byte-identical and covering layers/ingest/query/lint/index/log")


def check_query_fileback(inp: AuditInputs) -> dict:
    entries = _log_entries(inp.log_md or "")
    queries = sum(1 for h, _ in entries if LOG_HEADER.match(h) and " query | " in h)
    filed = [p["id"] for p in wiki_layer(inp.pages) if "query_fileback" in (p.get("tags") or []) and _has_source_ref(p)]
    return _result(queries >= 1 and len(filed) >= 1, {"query_log_entries": queries, "filed_back_pages": len(filed)},
                   ">=1 query log entry and >=1 cited filed-back page", {"sample": filed[:5]})


def _out_links(page: dict, by_stem: dict[str, str]) -> tuple[set[str], set[str]]:
    targets, broken = set(), set()
    for link in page.get("links") or []:
        (targets if link in by_stem.values() else broken).add(str(link))
    for stem in WIKILINK.findall(str(page.get("body") or "")):
        stem = stem.strip()
        if stem in by_stem:
            targets.add(by_stem[stem])
        elif stem in by_stem.values():
            targets.add(stem)
        else:
            broken.add(stem)
    targets.discard(page["id"])
    return targets, broken


def link_graph(pages: list[dict]) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    active = wiki_layer(pages)
    # Obsidian resolves [[target]] by file stem or by alias (the exported title).
    titles = Counter(str(p.get("title") or "").strip() for p in active)
    by_stem = {str(p.get("title") or "").strip(): p["id"] for p in active if titles[str(p.get("title") or "").strip()] == 1}
    by_stem.update({file_stem(p["id"]): p["id"] for p in active})
    out, broken, inbound = {}, {}, {p["id"]: set() for p in active}
    for page in active:
        out[page["id"]], broken[page["id"]] = _out_links(page, by_stem)
        if page.get("parent_page_id") in inbound:
            out[page["id"]].add(page["parent_page_id"])
    for source, targets in out.items():
        for target in targets:
            inbound.setdefault(target, set()).add(source)
    return out, inbound, broken


def check_lint(inp: AuditInputs) -> dict:
    out, inbound, broken = link_graph(inp.pages)
    broken_pages = {k: sorted(v) for k, v in broken.items() if v}
    orphans = sorted(k for k, v in inbound.items() if not v)
    cutoff = (inp.now - timedelta(days=1)).date().isoformat()
    recent_lint = any(m and m.group(2) == "lint" and m.group(1) >= cutoff
                      for m in (LOG_HEADER.match(h) for h, _ in _log_entries(inp.log_md or "")))
    ok = not broken_pages and not orphans and recent_lint
    return _result(ok, {"broken_link_pages": len(broken_pages), "orphans": len(orphans), "lint_logged_24h": recent_lint},
                   "0 broken links, 0 orphans, lint logged within 24h",
                   {"broken_sample": dict(list(broken_pages.items())[:5]), "orphan_sample": orphans[:10]})


def check_long_pages(inp: AuditInputs) -> dict:
    pages = wiki_layer(inp.pages)
    by_id = {p["id"]: p for p in pages}
    too_long = sorted(p["id"] for p in pages if len(str(p.get("body") or "")) > LONG_PAGE_LIMIT)
    broken = []
    for child in pages:
        parent_id = child.get("parent_page_id")
        if not parent_id:
            continue
        parent = by_id.get(parent_id)
        if not parent or (child["id"] not in (parent.get("links") or [])
                          and file_stem(child["id"]) not in str(parent.get("body") or "")):
            broken.append(child["id"])
    return _result(not too_long and not broken, {"too_long": len(too_long), "unlinked_children": len(broken)},
                   f"body <= {LONG_PAGE_LIMIT} chars; parent links every child",
                   {"too_long": too_long[:10], "unlinked_children": broken[:10]})


def check_cross_refs(inp: AuditInputs) -> dict:
    out, inbound, _ = link_graph(inp.pages)
    no_out = sorted(k for k, v in out.items() if not v)
    no_in = sorted(k for k, v in inbound.items() if not v)
    return _result(bool(out) and not no_out and not no_in, {"pages": len(out), "no_outbound": len(no_out), "no_inbound": len(no_in)},
                   "every page >=1 outbound and >=1 inbound link", {"no_outbound": no_out[:10], "no_inbound": no_in[:10]})


def _frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    keys = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            key, value = line.split(":", 1)
            keys[key.strip()] = value.strip()
    return keys


def check_frontmatter(inp: AuditInputs) -> dict:
    missing_file, bad = [], []
    for page in wiki_layer(inp.pages):
        text = inp.markdown.get(file_stem(page["id"]))
        if text is None:
            missing_file.append(page["id"])
            continue
        keys = _frontmatter(text)
        if keys is None or any(k not in keys for k in FRONTMATTER_KEYS):
            bad.append(page["id"])
    return _result(not missing_file and not bad and bool(inp.pages), {"missing_file": len(missing_file), "bad_frontmatter": len(bad)},
                   f"every page exported with YAML keys {', '.join(FRONTMATTER_KEYS)}",
                   {"missing_file": missing_file[:10], "bad": bad[:10]})


def check_freshness(inp: AuditInputs) -> dict:
    cutoff = (inp.now - timedelta(days=1)).date().isoformat()
    recent_ingest = any(m and m.group(2) == "ingest" and m.group(1) >= cutoff
                        for m in (LOG_HEADER.match(h) for h, _ in _log_entries(inp.log_md or "")))
    unhealthy = sorted(name for name, row in inp.collection_health.items() if not row.get("healthy"))
    ok = recent_ingest and bool(inp.collection_health) and not unhealthy
    return _result(ok, {"ingest_24h": recent_ingest, "sources": len(inp.collection_health), "unhealthy": len(unhealthy)},
                   "ingest within 24h and every enabled source healthy",
                   {"unhealthy": {n: inp.collection_health[n] for n in unhealthy}})


CHECKS = {
    "C1_raw_immutable": check_raw_immutable,
    "C2_knowledge_not_links": check_knowledge_not_links,
    "C3_source_summaries": check_source_summaries,
    "C4_entity_topic_pages": check_entity_topic_pages,
    "C5_compounding": check_compounding,
    "C6_index_md": check_index_md,
    "C7_log_md": check_log_md,
    "C8_schema": check_schema,
    "C9_query_fileback": check_query_fileback,
    "C10_lint": check_lint,
    "C11_long_pages": check_long_pages,
    "C12_cross_refs": check_cross_refs,
    "C13_frontmatter": check_frontmatter,
    "C14_freshness": check_freshness,
}


def run_audit(inp: AuditInputs) -> dict:
    results = {}
    for name, check in CHECKS.items():
        try:
            results[name] = check(inp)
        except Exception as exc:  # a crashing check is a failing check, never a silent pass
            results[name] = _result(False, None, "check executed", {"error": f"{type(exc).__name__}: {exc}"[:300]})
    return {"ok": all(r["pass"] for r in results.values()), "checked_at": inp.now.isoformat(timespec="seconds"),
            "results": results}


# ---------------------------------------------------------------- live inputs

def _journal_roots() -> list[Path]:
    from reports import raw_archive

    root = raw_archive.reports_root()
    return [p for p in [root / "wiki-source-v1", *sorted(root.glob("public-wiki-*"))] if (p / "sources").is_dir()]


def _load_compiled_sources() -> dict[str, dict]:
    sources: dict[str, dict] = {}
    for root in _journal_roots():
        for path in sorted((root / "sources").glob("*.json")):
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if receipt.get("compile_state") != "compiled" or not isinstance(receipt.get("source"), dict):
                continue
            sources[str(receipt.get("revision_id"))] = {**receipt["source"], "compile_state": "compiled"}
    return sources


def _read_artifact(path: str) -> bytes | None:
    from reports import raw_archive

    if not path:
        return None
    data = raw_archive.read_raw_artifact(path)
    return data.encode("utf-8") if isinstance(data, str) else data


def collection_health_from(rows: dict, stale: list[dict]) -> dict[str, dict]:
    """Healthy = not blocked, latest fetch succeeded, not past its stale threshold.

    Sources explicitly marked ``disabled`` are not enabled collection sources.
    """
    stale_names = {row.get("source") for row in stale or []}
    health: dict[str, dict] = {}
    for name, row in (rows or {}).items():
        if not isinstance(row, dict) or row.get("availability") == "disabled":
            continue
        healthy = (row.get("availability") != "blocked" and row.get("fetch_success", True) is not False
                   and name not in stale_names)
        health[str(name)] = {"healthy": healthy, "availability": row.get("availability"),
                             "last_success": row.get("last_success"), "last_error": str(row.get("last_error") or "")[:160]}
    return health


def _collection_health() -> dict[str, dict]:
    from reports import source_collector

    rows = source_collector.load_source_health()
    return collection_health_from(rows, source_collector.stale_sources(rows))


def baseline_path() -> Path:
    from agent_console import wiki

    return wiki.wiki_artifacts_dir() / ".karpathy-log-baseline.json"


def live_inputs() -> AuditInputs:
    from agent_console import qmd_search, wiki, wiki_contract

    wiki._CACHE.clear()
    pages = wiki._all_wiki_pages()
    art = wiki.wiki_artifacts_dir()

    def read(name: str) -> str | None:
        path = art / name
        return path.read_text(encoding="utf-8") if path.exists() else None

    md_dir = Path(qmd_search.wiki_docs_dir())
    markdown = {}
    if md_dir.is_dir():
        markdown = {p.stem: p.read_text(encoding="utf-8", errors="replace")[:4000] for p in md_dir.glob("*.md")}
    baseline = None
    if baseline_path().exists():
        baseline = json.loads(baseline_path().read_text(encoding="utf-8"))
    return AuditInputs(pages=pages, index_md=read("index.md"), log_md=read("log.md"), log_baseline=baseline,
                       schema_export=read("schema.md"), schema_source=wiki_contract.SCHEMA_PATH.read_text(encoding="utf-8"),
                       markdown=markdown, compiled_sources=_load_compiled_sources(), read_artifact=_read_artifact,
                       collection_health=_collection_health())


def record_log_baseline(log_md: str) -> dict:
    data = log_md.encode("utf-8")
    return {"size": len(data), "sha256": _sha(data)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the full JSON report")
    parser.add_argument("--record-log-baseline", action="store_true",
                        help="after a passing append-only check, store the current log prefix for the next run")
    args = parser.parse_args(argv)
    inp = live_inputs()
    report = run_audit(inp)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    else:
        for name, row in report["results"].items():
            print(f"{'PASS' if row['pass'] else 'FAIL'} {name}: {json.dumps(row['metric'], ensure_ascii=False)}"
                  f" (threshold: {row['threshold']})")
        print("OVERALL", "PASS" if report["ok"] else "FAIL")
    if args.record_log_baseline and inp.log_md and report["results"]["C7_log_md"]["evidence"].get("append_only"):
        baseline_path().write_text(json.dumps(record_log_baseline(inp.log_md)), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
