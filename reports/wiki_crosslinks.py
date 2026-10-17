"""Cross-reference maintenance: related-page links and category overview hubs.

Every active wiki page gets outbound links to its most related pages (shared
ticker/topic tags, overlapping title terms) and belongs to a category overview
hub, so no page is an orphan. Hubs are `overview` pages whose children list at
most HUB_CHILD_SIZE members each; they never rewrite member prose.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict

from agent_console import qmd_search, shared_memory, wiki

RELATED_PER_PAGE = 5
HUB_CHILD_SIZE = 25
HUB_PREFIX = "overview-"
_GENERIC_TAGS = {"wiki", "market", "ticker", "draft", "reviewed", "stable", "archived", "source_digest",
                 "compiled_source", "conversation", "query_fileback", "overview", *wiki.VALID_KINDS}
_HUB_TITLES = {
    "종합 위키 포털 (Overview Portals)": "포털 및 개요 문서",
    "원문 지식 요약 (Compiled Source Summaries)": "원문 지식 요약",
    "정치·지정학 (Politics & Geopolitics)": "정치·지정학 테마 위키",
    "금리·통화정책 (Monetary Policy & Rates)": "금리·통화정책 테마 위키",
    "거시경제·에너지 (Macro & Commodities)": "거시경제·에너지 테마 위키",
    "기술·반도체·AI (Tech & AI)": "기술·반도체·AI 테마 위키",
    "기업실적·산업동향 (Earnings & Industry)": "기업실적·산업동향 테마 위키",
    "종목전략·플레이북 (Single Stock Strategies)": "종목전략·플레이북 테마 위키",
    "Other": "기타 지식"
}
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_STOP = {"수집", "소스", "위키", "근거", "정리", "the", "and", "of", "for", "관련", "신호", "검증", "위험", "리스크"}


def _informative_tags(page: dict) -> set[str]:
    return {t for t in page.get("tags") or [] if t not in _GENERIC_TAGS and not t.startswith(
        ("source:", "source_text_scope:", "source_provenance:", "split_", "merged_", "merge_", "category:"))}


def _terms(page: dict) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(str(page.get("title") or "")) if t.lower() not in _STOP}


def _score(a: dict, b: dict, tags: dict, terms: dict) -> float:
    shared = tags[a["id"]] & tags[b["id"]]
    tickers = sum(1 for t in shared if t.startswith("ticker:"))
    union = terms[a["id"]] | terms[b["id"]]
    overlap = len(terms[a["id"]] & terms[b["id"]]) / len(union) if union else 0.0
    score = 3.0 * tickers + (len(shared) - tickers) + 4.0 * overlap
    return score if (tickers or len(shared) >= 2 or overlap >= 0.25) else 0.0


def hub_id(category: str, index: int | None = None) -> str:
    slug = hashlib.sha256(category.encode()).hexdigest()[:10]
    return f"{HUB_PREFIX}{slug}" + ("" if index is None else f"-{index:02d}")


def is_hub(page: dict) -> bool:
    return str(page.get("id") or "").startswith(HUB_PREFIX)


def plan(pages: list[dict]) -> dict:
    """Return {"links": {page_id: [ids]}, "hubs": [page payloads]} without writing."""
    active = [p for p in pages if p.get("status") != "archived" and not wiki.is_raw_layer_page(p) and not is_hub(p)]
    ids = {p["id"] for p in active}
    tags = {p["id"]: _informative_tags(p) for p in active}
    terms = {p["id"]: _terms(p) for p in active}
    by_tag: dict[str, set[str]] = defaultdict(set)
    by_term: dict[str, set[str]] = defaultdict(set)
    for p in active:
        for t in tags[p["id"]]:
            by_tag[t].add(p["id"])
        for t in terms[p["id"]]:
            by_term[t].add(p["id"])
    by_id = {p["id"]: p for p in active}
    links: dict[str, list[str]] = {}
    for p in active:
        pool = set().union(*(by_tag[t] for t in tags[p["id"]]), *(by_term[t] for t in terms[p["id"]] if len(by_term[t]) < 60))
        pool.discard(p["id"])
        ranked = sorted(((s, q) for q in pool if (s := _score(p, by_id[q], tags, terms)) > 0), key=lambda r: (-r[0], r[1]))
        existing = [link for link in p.get("links") or [] if link in ids]
        related = [q for _, q in ranked if q not in existing][:RELATED_PER_PAGE]
        # Cap here (the store caps at MAX_LINKS) so a re-run sees exactly what it wrote.
        links[p["id"]] = (existing + related)[:wiki.MAX_LINKS]

    hubs, members = [], defaultdict(list)
    for p in active:
        if p.get("parent_page_id") in ids:
            continue  # split children are reached through their parent
        members[wiki._index_category(p)].append(p)
    for category, rows in sorted(members.items()):
        rows = sorted(rows, key=lambda r: str(r.get("title") or ""))
        chunks = [rows[i:i + HUB_CHILD_SIZE] for i in range(0, len(rows), HUB_CHILD_SIZE)]
        parent = hub_id(category)
        title = f"{_HUB_TITLES.get(category, category)} 개요"
        child_ids = [hub_id(category, n + 1) for n in range(len(chunks))] if len(chunks) > 1 else []
        for n, chunk in enumerate(chunks):
            owner = child_ids[n] if child_ids else parent
            for member in chunk:
                links[member["id"]] = list(dict.fromkeys([owner, *links[member["id"]]]))[:wiki.MAX_LINKS]
        if child_ids:
            body = [f"{title}: {len(rows)}개 문서를 {len(chunks)}개 하위 목록으로 나눴습니다.", ""]
            body += [f"- [[{_stem(cid)}|{title} {n + 1}]] — {chunks[n][0]['title'][:30]} ~ {chunks[n][-1]['title'][:30]}"
                     for n, cid in enumerate(child_ids)]
            hubs.append(_hub(parent, title, category, "\n".join(body), child_ids, None, len(rows)))
            for n, (cid, chunk) in enumerate(zip(child_ids, chunks)):
                hubs.append(_hub(cid, f"{title} {n + 1}", category, _member_list(chunk), [], parent, len(chunk)))
        else:
            hubs.append(_hub(parent, title, category, _member_list(chunks[0]), [], None, len(rows)))
    root_links = [h["id"] for h in hubs if not h.get("parent_page_id")]
    root = _hub(HUB_PREFIX + "root", "LLM 위키 대문", "Overviews", "\n".join(
        [f"- [[{_stem(h['id'])}|{h['title']}]] — {h['summary']}" for h in hubs if not h.get("parent_page_id")]),
        root_links, None, len(active))
    for h in hubs:
        if not h.get("parent_page_id"):
            h["links"] = [root["id"], *h["links"]][:wiki.MAX_LINKS]
    return {"links": links, "hubs": [root, *hubs]}


def _stem(page_id: str) -> str:
    return qmd_search._safe_file_stem(qmd_search._clean(page_id, 100))


def _member_list(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        title = re.sub(r"[\[\]|]+", " ", str(row.get("title") or row["id"]))[:80]
        summary = re.sub(r"\s+", " ", str(row.get("summary") or ""))[:90]
        lines.append(f"- [[{_stem(row['id'])}|{title}]] — {summary}")
    return "\n".join(lines)


def _hub(page_id, title, category, body, links, parent, count) -> dict:
    return {"id": page_id, "title": title, "kind": "overview", "surface": "wiki", "status": "draft",
            "tags": ["wiki", "overview", f"category:{category}"], "summary": f"{category} {count}개 문서 목록",
            "body": body, "links": list(links)[:wiki.MAX_LINKS], "parent_page_id": parent or ""}


def apply(dry_run: bool = True) -> dict:
    """Recompute under the store lock and write links + hubs in one rewrite."""
    with shared_memory._events_lock():
        rows = shared_memory._read_jsonl_checked(shared_memory._paths()["events"])
        wiki_rows = [row for row in rows if wiki._is_wiki_record(row)]
        pages = {p["id"]: p for p in (wiki._record_to_page(row) for row in wiki_rows)}
        result = plan(list(pages.values()))
        upserts = {}
        for page_id, links in result["links"].items():
            if links != list(pages[page_id].get("links") or []):
                upserts[page_id] = wiki._build_wiki_record({**pages[page_id], "links": links}, existing=pages[page_id])
        stale_hubs = [p for p in pages.values() if is_hub(p) and p["id"] not in {h["id"] for h in result["hubs"]}
                      and p.get("status") != "archived"]
        for hub in [*result["hubs"], *({**p, "status": "archived"} for p in stale_hubs)]:
            current = pages.get(hub["id"])
            record = wiki._build_wiki_record(hub, existing=current or {})
            stored = wiki._record_to_page(shared_memory.normalize_record(record))
            if not current or any(current.get(k) != stored.get(k) for k in ("body", "links", "title", "status", "parent_page_id")):
                upserts[hub["id"]] = record
        summary = {"dry_run": dry_run, "pages_relinked": sum(1 for k in upserts if not k.startswith(HUB_PREFIX)),
                   "hubs_written": sum(1 for k in upserts if k.startswith(HUB_PREFIX)), "hubs": len(result["hubs"])}
        if dry_run or not upserts:
            return summary
        normalized = {k: shared_memory.normalize_record(v) for k, v in upserts.items()}
        shared_memory._write_jsonl_locked([normalized.pop(row["id"], row) for row in rows] + list(normalized.values()))
        shared_memory._write_index_locked()
    wiki._CACHE.clear()
    wiki._append_wiki_log("lint", title=f"cross-references: {summary['pages_relinked']} pages relinked",
                          relinked=summary["pages_relinked"], hubs=summary["hubs"])
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(apply(dry_run=not args.apply), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
