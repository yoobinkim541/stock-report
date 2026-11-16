"""위키 온톨로지 갱신과 인사이트 생성 배치.

    python -m reports.wiki_ontology_insights            # 점검만 (LLM 호출·쓰기 없음)
    python -m reports.wiki_ontology_insights --apply    # 관계 추출 → 반대 검토 → 온톨로지 컴파일 → 인사이트 작성

1. 관계 추출: claim 이 바뀐 페이지만 묶어 LLM 에 보내고, 인용 claim 에 두 개체가 실제로 나오는 관계만 남긴다.
2. 반대 검토: 추출과 다른 제공자를 먼저 부르는 검토자가 같은 claim 을 보고 각 관계를 반박한다. 판정을 어떻게
   반영할지는 ``wiki_ontology.validate_review`` 와 ``compile_ontology`` 의 규칙이 정한다 (모델이 정하지 않는다).
3. 컴파일: ``agent_console.wiki_ontology.compile_ontology`` (LLM 없음, 매번 전체 재계산). 발행자는 소스 저널에서 읽는다.
4. 인사이트: 여러 출처의 claim 이 모인 개체마다 ``kind=insight`` 페이지를 하나 유지한다. 모든 판단은 서로 다른
   출처의 claim 2건 이상을 인용해야 하고, 인용 claim 에 없는 숫자가 들어가면 그 판단은 버린다.

호출 수는 ``WIKI_ONTOLOGY_EXTRACT_CALLS`` (기본 6), ``WIKI_ONTOLOGY_REVIEW_CALLS`` (기본 6),
``WIKI_INSIGHT_MAX_PER_RUN`` (기본 3) 으로 제한한다. 검토자 제공자 순서는 ``WIKI_ONTOLOGY_REVIEW_PROVIDERS``
(기본: ``WIKI_STRUCTURED_PROVIDERS`` 를 한 칸 돌린 순서).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv          # crons/*.py 관례 — uv run 은 .env 를 자동 주입 안 함
load_dotenv()                           # 없으면 Codex 가 계정에서 못 쓰는 기본 모델로 떠서 Hermes 혼자 답한다

from agent_console import wiki, wiki_ontology
from agent_console.wiki_ontology import ENTITY_TAG_PREFIX, ENTITY_TYPES, INSIGHT_KIND
from reports.source_publishers import ROLES, publisher

# 운영 실측(2026-11-15, Codex medium): claim 13건 51초, 44건 78초. Codex 호출 상한이 90초라 30건으로 묶는다.
MAX_CLAIMS_PER_CALL = 30
MAX_CHARS_PER_CALL = 7000
PROVIDER_TIMEOUT = 120
MAX_INSIGHT_CLAIMS = 28
MIN_INSIGHT_CLAIMS = 4
MIN_INSIGHT_SOURCES = 3
# 지역은 claim 대부분에 등장해 판단 단위가 되지 못한다 (미국 449건). 관계의 끝점으로만 쓴다.
INSIGHT_ENTITY_TYPES = tuple(name for name in ENTITY_TYPES if name != "region")
REFRESH_MIN_NEW_CLAIMS = 3
REFRESH_MAX_AGE_DAYS = 7
_HORIZONS = {"days": "며칠", "weeks": "몇 주", "months": "몇 달"}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

# 응답 본문만 돌려주거나, (본문, 답한 제공자 이름) 을 돌려준다.
LlmFn = Callable[[str], "str | None | tuple[str | None, str]"]
# 검토 호출: (프롬프트, 그 관계를 제안한 제공자들). 제안한 쪽은 자기 제안을 판정하지 못한다.
ReviewFn = Callable[[str, frozenset], "str | None | tuple[str | None, str]"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_llm(prompt: str) -> tuple[str | None, str]:
    from lib.llm_cli import structured_chat_with_provider

    return structured_chat_with_provider(prompt, provider_timeout=PROVIDER_TIMEOUT)


def review_provider_order() -> str | None:
    """검토자가 부를 제공자 순서. 따로 정하지 않으면 추출 순서를 한 칸 돌려 다른 모델이 먼저 답하게 한다."""
    from lib.llm_cli import structured_providers

    configured = os.getenv("WIKI_ONTOLOGY_REVIEW_PROVIDERS", "").strip()
    if configured:
        return configured
    names = structured_providers() or []
    return ",".join(names[1:] + names[:1]) or None


def _ask_others(prompt: str, order: str | None, barred: frozenset) -> tuple[str | None, str]:
    """``order`` 에서 ``barred`` (그 관계를 제안한 제공자)를 뺀 나머지에게만 묻는다. 남는 곳이 없으면 묻지 않는다."""
    from lib.llm_cli import structured_chat_with_provider, structured_providers

    names = [name for name in structured_providers(order) or [] if name not in barred]
    if not names:
        return None, ""
    return structured_chat_with_provider(prompt, provider_timeout=PROVIDER_TIMEOUT, providers=",".join(names))


def _default_review_llm(prompt: str, barred: frozenset = frozenset()) -> tuple[str | None, str]:
    return _ask_others(prompt, review_provider_order(), barred)


def _default_cross_llm(prompt: str, barred: frozenset = frozenset()) -> tuple[str | None, str]:
    """검토자가 더한 관계의 되검토 — 추출과 같은 순서로 부르되 그 검토자는 뺀다."""
    return _ask_others(prompt, None, barred)


def _parse(text: str | None) -> dict | None:
    from lib.structured_output import parse_object

    return parse_object(text) if isinstance(text, str) else None


def _reply(value: object) -> tuple[dict | None, str]:
    text, provider = value if isinstance(value, tuple) else (value, "")
    return _parse(text), str(provider or "")


def source_providers() -> dict[str, str]:
    """source revision id → 수집 출처 이름 (``telegram:kwusa``). 위키 소스 저널의 원장에서 읽는다."""
    from reports import raw_archive
    from reports.wiki_pipeline_health import SOURCE_PIPELINE_JOURNALS

    out: dict[str, str] = {}
    for _, directory in SOURCE_PIPELINE_JOURNALS:
        for path in (raw_archive.reports_root() / directory / "sources").glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            source = data.get("source") if isinstance(data, dict) else None
            if isinstance(source, dict) and source.get("provider"):
                out[str(data.get("revision_id") or path.stem)] = str(source["provider"])
    return out


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# ── 1. 관계 추출 ────────────────────────────────────────────────────────

def pending_pages(pages: list[dict], extractions: dict) -> list[dict]:
    """claim 이 추출 캐시와 다른 페이지. 최근에 바뀐 지식부터."""
    records = extractions.get("pages") or {}
    rows = [page for page in wiki_ontology.knowledge_pages(pages)
            if (records.get(str(page.get("id"))) or {}).get("input_hash") != wiki_ontology.page_input_hash(page)]
    return sorted(rows, key=lambda page: (str(page.get("updated_at") or ""), str(page.get("id"))), reverse=True)


def batches(pages: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    current: list[dict] = []
    claims = chars = 0
    for page in pages:
        rows = wiki_ontology.page_claims(page)
        size = sum(min(len(str(claim["text"])), 600) for claim in rows)
        if current and (claims + len(rows) > MAX_CLAIMS_PER_CALL or chars + size > MAX_CHARS_PER_CALL):
            out.append(current)
            current, claims, chars = [], 0, 0
        current.append(page)
        claims += len(rows)
        chars += size
    if current:
        out.append(current)
    return out


def extract_relations(pages: list[dict], extractions: dict, *, llm_fn: LlmFn, max_calls: int,
                      save: Callable[[dict], None] | None = None) -> dict:
    """대기 페이지를 ``max_calls`` 번까지 추출해 ``extractions`` 를 갱신한다."""
    lexicon = wiki_ontology.seed_lexicon(wiki_ontology.learned_entities(extractions))
    summary = {"calls": 0, "failed_calls": 0, "pages": 0, "relations": 0, "new_entities": 0, "rejected": {}}
    failures = 0
    for batch in batches(pending_pages(pages, extractions))[:max(0, max_calls)]:
        prompt, keymap = wiki_ontology.build_extraction_request(batch, lexicon)
        summary["calls"] += 1
        payload, provider = _reply(llm_fn(prompt))
        if payload is None:
            summary["failed_calls"] += 1
            failures += 1
            if failures >= 2:
                break  # 공급자 장애로 보고 다음 실행에 맡긴다
            continue
        failures = 0
        result = wiki_ontology.validate_extraction(payload, keymap, lexicon)
        for entity in result["entities"]:
            lexicon.add(entity)
        for code, count in result["rejected"].items():
            summary["rejected"][code] = summary["rejected"].get(code, 0) + count
        by_page: dict[str, dict] = {
            str(page["id"]): {"input_hash": wiki_ontology.page_input_hash(page), "extracted_at": _now(),
                              "extractor": provider, "entities": [], "relations": []}
            for page in batch
        }
        entities = {entity["id"]: entity for entity in result["entities"]}
        for relation in result["relations"]:
            record = by_page[relation["evidence"][0]["page_id"]]
            record["relations"].append(relation)
            for side in ("from", "to"):
                entity = entities.get(relation[side])
                if entity and entity not in record["entities"]:
                    record["entities"].append(entity)
        extractions.setdefault("pages", {}).update(by_page)
        summary["pages"] += len(batch)
        summary["relations"] += len(result["relations"])
        summary["new_entities"] += len(result["entities"])
        if save:
            save(extractions)
    return summary


# ── 2. 반대 검토 ────────────────────────────────────────────────────────

def review_batches(pages: list[dict], extractions: dict, *, cross: bool = False
                   ) -> list[tuple[list[dict], list[dict]]]:
    """(프롬프트에 넣을 페이지, 그중 검토 대기 페이지) 묶음. 최근 추출부터.

    관계가 같은 추출 묶음의 다른 페이지 claim 을 인용할 수 있어, 그 페이지도 함께 넣는다.
    ``cross=True`` 는 검토자가 더한 관계의 되검토를 기다리는 페이지를 묶는다.
    한 묶음에는 제안한 제공자가 같은 페이지만 넣는다 — 섞이면 둘 다 판정에서 빠져 물어볼 곳이 없어진다.
    """
    records = extractions.get("pages") or {}
    pages_by_id = {str(page.get("id")): page for page in wiki_ontology.knowledge_pages(pages)}
    waits = wiki_ontology.needs_cross_review if cross else wiki_ontology.needs_review
    pending = [page for page_id, page in pages_by_id.items() if waits(records.get(page_id) or {}, page)]
    pending.sort(key=lambda page: (str(records[str(page["id"])].get("extracted_at") or ""), str(page["id"])), reverse=True)

    def proposer(page: dict) -> str:
        record = records[str(page["id"])]
        return str((record["review"].get("reviewer") if cross else record.get("extractor")) or "")

    out: list[tuple[list[dict], list[dict]]] = []
    shown: dict[str, dict] = {}
    waiting: list[dict] = []
    claims = chars = 0
    for page in pending:
        group = {str(page["id"]): page}
        for relation in records[str(page["id"])].get("relations") or []:
            for item in relation.get("evidence") or []:
                other = pages_by_id.get(str(item.get("page_id")))
                if other is not None:
                    group.setdefault(str(other["id"]), other)
        fresh = {page_id: row for page_id, row in group.items() if page_id not in shown}
        rows = [claim for row in fresh.values() for claim in wiki_ontology.page_claims(row)]
        size = sum(min(len(str(claim["text"])), 600) for claim in rows)
        if waiting and (claims + len(rows) > MAX_CLAIMS_PER_CALL or chars + size > MAX_CHARS_PER_CALL
                        or proposer(page) != proposer(waiting[-1])):
            out.append((list(shown.values()), waiting))
            shown, waiting, claims, chars = {}, [], 0, 0
            fresh = group
            rows = [claim for row in fresh.values() for claim in wiki_ontology.page_claims(row)]
            size = sum(min(len(str(claim["text"])), 600) for claim in rows)
        shown.update(fresh)
        waiting.append(page)
        claims += len(rows)
        chars += size
    if waiting:
        out.append((list(shown.values()), waiting))
    return out


def _barred(names) -> frozenset:
    return frozenset(name for name in names if name)


def review_relations(pages: list[dict], extractions: dict, *, llm_fn: ReviewFn, max_calls: int,
                     cross_llm_fn: ReviewFn | None = None, save: Callable[[dict], None] | None = None) -> dict:
    """검토 대기 페이지의 관계를 ``max_calls`` 번까지 반대 검토해 판정을 추출 캐시에 적는다.

    ``cross_llm_fn`` 이 있으면 검토자가 더한 관계를 그 함수(추출 쪽 모델)로 다시 ``max_calls`` 번까지 되검토한다.
    어느 쪽 모델의 제안이든 반대편 판정을 거쳐야 그래프에 확정으로 남는다. 제안한 제공자가 답한 판정은 받지 않고
    (``refused_same_model``) 그 페이지를 대기로 남긴다 — 다른 모델이 답할 수 있을 때 다시 묻는다.
    """
    lexicon = wiki_ontology.seed_lexicon(wiki_ontology.learned_entities(extractions))
    records = extractions.get("pages") or {}
    summary = {"calls": 0, "failed_calls": 0, "pages": 0, "relations": 0, "keep": 0, "view": 0, "reject": 0,
               "unreviewed": 0, "added": 0, "refused_same_model": 0, "reviewers": {}, "rejected": {}}
    failures = 0
    for shown, waiting in review_batches(pages, extractions)[:max(0, max_calls)]:
        proposals = [relation for page in waiting for relation in records[str(page["id"])].get("relations") or []
                     if relation.get("origin") != "reviewer"]
        prompt, keymap, relmap = wiki_ontology.build_review_request(shown, proposals, lexicon)
        barred = _barred(records[str(page["id"])].get("extractor") for page in waiting)
        summary["calls"] += 1
        payload, provider = _reply(llm_fn(prompt, barred))
        if provider in barred:
            summary["refused_same_model"] += 1
            continue
        result = wiki_ontology.validate_review(payload, relmap, keymap, lexicon)
        if result["rejected"].get("payload_invalid"):
            summary["failed_calls"] += 1
            failures += 1
            if failures >= 2:
                break  # 공급자 장애로 보고 다음 실행에 맡긴다
            continue
        failures = 0
        for code, count in {**result["rejected"], **result["missed"]["rejected"]}.items():
            summary["rejected"][code] = summary["rejected"].get(code, 0) + count
        reviewed = {id(relation): result["reviews"].get(key) for key, relation in relmap.items()}
        for relation in proposals:
            review = reviewed.get(id(relation))
            if review:
                relation["review"] = review
            else:
                relation.pop("review", None)
            summary[review["verdict"] if review else "unreviewed"] += 1
        waiting_ids = [str(page["id"]) for page in waiting]
        for page_id in waiting_ids:
            record = records[page_id]
            record["relations"] = [relation for relation in record["relations"] if relation.get("origin") != "reviewer"]
            record["review"] = {"input_hash": record["input_hash"], "reviewed_at": _now(), "reviewer": provider}
        entities = {entity["id"]: entity for entity in result["missed"]["entities"]}
        for entity in entities.values():
            lexicon.add(entity)
        for relation in result["missed"]["relations"]:
            home = relation["evidence"][0]["page_id"]
            record = records[home if home in waiting_ids else waiting_ids[0]]
            record["relations"].append({**relation, "origin": "reviewer"})
            for side in ("from", "to"):
                entity = entities.get(relation[side])
                if entity and entity not in record["entities"]:
                    record["entities"].append(entity)
        summary["pages"] += len(waiting)
        summary["relations"] += len(proposals)
        summary["added"] += len(result["missed"]["relations"])
        if provider:
            summary["reviewers"][provider] = summary["reviewers"].get(provider, 0) + 1
        if save:
            save(extractions)
    if cross_llm_fn:
        summary["cross"] = _cross_review(pages, extractions, lexicon, llm_fn=cross_llm_fn, max_calls=max_calls, save=save)
    return summary


def _cross_review(pages: list[dict], extractions: dict, lexicon: wiki_ontology.Lexicon, *, llm_fn: ReviewFn,
                  max_calls: int, save: Callable[[dict], None] | None) -> dict:
    """검토자가 더한 관계를 반대편(추출 쪽 모델)이 되검토한다. 판정만 받고 새 관계는 받지 않아 여기서 끝난다."""
    records = extractions.get("pages") or {}
    summary = {"calls": 0, "failed_calls": 0, "pages": 0, "relations": 0, "keep": 0, "view": 0, "reject": 0,
               "unreviewed": 0, "refused_same_model": 0, "reviewers": {}}
    failures = 0
    for shown, waiting in review_batches(pages, extractions, cross=True)[:max(0, max_calls)]:
        proposals = [relation for page in waiting for relation in records[str(page["id"])].get("relations") or []
                     if relation.get("origin") == "reviewer"]
        prompt, keymap, relmap = wiki_ontology.build_review_request(shown, proposals, lexicon, additions=False)
        barred = _barred(records[str(page["id"])]["review"].get("reviewer") for page in waiting)
        summary["calls"] += 1
        payload, provider = _reply(llm_fn(prompt, barred))
        if provider in barred:
            summary["refused_same_model"] += 1
            continue
        result = wiki_ontology.validate_review(payload, relmap, keymap, lexicon)
        if result["rejected"].get("payload_invalid"):
            summary["failed_calls"] += 1
            failures += 1
            if failures >= 2:
                break  # 공급자 장애로 보고 다음 실행에 맡긴다
            continue
        failures = 0
        reviewed = {id(relation): result["reviews"].get(key) for key, relation in relmap.items()}
        for relation in proposals:
            review = reviewed.get(id(relation))
            if review:
                relation["review"] = review
            else:
                relation.pop("review", None)
            summary[review["verdict"] if review else "unreviewed"] += 1
        for page in waiting:
            records[str(page["id"])]["review"]["cross"] = {"reviewed_at": _now(), "reviewer": provider}
        summary["pages"] += len(waiting)
        summary["relations"] += len(proposals)
        if provider:
            summary["reviewers"][provider] = summary["reviewers"].get(provider, 0) + 1
        if save:
            save(extractions)
    return summary


def prune_extractions(pages: list[dict], extractions: dict) -> int:
    """사라졌거나 보관된 페이지의 추출 캐시를 지운다."""
    alive = {str(page.get("id")) for page in wiki_ontology.knowledge_pages(pages)}
    stale = [page_id for page_id in extractions.get("pages") or {} if page_id not in alive]
    for page_id in stale:
        del extractions["pages"][page_id]
    return len(stale)


# ── 3. 인사이트 ─────────────────────────────────────────────────────────

def insight_page_id(entity_id: str) -> str:
    return "insight-" + hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:20]


def _insight_meta(page: dict) -> dict:
    knowledge = page.get("source_knowledge")
    if not isinstance(knowledge, dict) and isinstance(page.get("raw"), dict):
        knowledge = page["raw"].get("source_knowledge")
    meta = knowledge.get("insight") if isinstance(knowledge, dict) else None
    return meta if isinstance(meta, dict) else {}


def _entity_evidence(ontology: dict, entity: dict, pages_by_id: dict[str, dict],
                     providers: dict[str, str] | None = None) -> list[dict]:
    """개체의 claim 을 관계 근거 → 최신 순으로, 출처가 고루 섞이게 고른다."""
    providers = providers or {}
    relation_claims = {tuple(pair) for relation in wiki_ontology.entity_relations(ontology, entity["id"])
                       for pair in relation["evidence"]}
    rows, seen_text = [], set()
    for page_id, claim_id in entity.get("claims") or []:
        page = pages_by_id.get(page_id)
        claim = next((item for item in wiki_ontology.page_claims(page or {}) if item["claim_id"] == claim_id), None)
        if not claim:
            continue
        text = " ".join(str(claim["text"]).split())
        if text in seen_text:
            continue
        seen_text.add(text)
        rows.append({
            "page_id": page_id, "claim_id": claim_id, "text": text, "title": str(page.get("title") or ""),
            "revision": str(claim.get("source_revision_id") or page_id),
            "publisher": _publisher_label(providers.get(str(claim.get("source_revision_id") or page_id))),
            "day": wiki_ontology._iso_day(claim.get("as_of"), page.get("updated_at"), page.get("created_at")),
            "in_relation": (page_id, claim_id) in relation_claims,
        })
    rows.sort(key=lambda row: (row["in_relation"], row["day"]), reverse=True)
    by_revision: dict[str, list[dict]] = {}
    for row in rows:
        by_revision.setdefault(row["revision"], []).append(row)
    picked: list[dict] = []
    while len(picked) < MAX_INSIGHT_CLAIMS and any(by_revision.values()):
        for queue in by_revision.values():
            if queue and len(picked) < MAX_INSIGHT_CLAIMS:
                picked.append(queue.pop(0))
    return picked


def _publisher_label(provider: str | None) -> str:
    if not provider:
        return "발행자 미상"
    who = publisher(provider)
    return f"{who['name']}·{ROLES.get(who['role'], who['role'])}"


def _evidence_hash(evidence: list[dict], relations: list[dict]) -> str:
    rows = sorted(f"{row['page_id']}|{row['claim_id']}" for row in evidence) + sorted(r["id"] for r in relations)
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def insight_candidates(ontology: dict, pages: list[dict], *, held: set[str] | None = None, now: str | None = None,
                       providers: dict[str, str] | None = None) -> list[dict]:
    """인사이트를 새로 쓰거나 고쳐 쓸 개체를 우선순위대로."""
    held = {ticker.upper() for ticker in held or set()}
    now_dt = datetime.fromisoformat(now or _now())
    pages_by_id = {str(page.get("id")): page for page in pages}
    existing = {wiki_ontology.insight_entity_id(page).casefold(): page for page in pages
                if page.get("kind") == INSIGHT_KIND and page.get("status") != "archived"}
    out = []
    for entity in (ontology.get("entities") or {}).values():
        if entity["type"] not in INSIGHT_ENTITY_TYPES or entity.get("source_count", 0) < MIN_INSIGHT_SOURCES:
            continue
        evidence = _entity_evidence(ontology, entity, pages_by_id, providers)
        if len(evidence) < MIN_INSIGHT_CLAIMS or len({row["revision"] for row in evidence}) < MIN_INSIGHT_SOURCES:
            continue
        relations = wiki_ontology.entity_relations(ontology, entity["id"])
        input_hash = _evidence_hash(evidence, relations)
        previous = existing.get(entity["id"].casefold())
        meta = _insight_meta(previous) if previous else {}
        if previous:
            if meta.get("input_hash") == input_hash:
                continue
            old = {tuple(pair) for pair in meta.get("evidence") or []}
            fresh = sum((row["page_id"], row["claim_id"]) not in old for row in evidence)
            relations_changed = sorted(meta.get("relations") or []) != sorted(r["id"] for r in relations)
            try:
                age = now_dt - datetime.fromisoformat(str(meta.get("generated_at")))
            except ValueError:
                age = timedelta(days=REFRESH_MAX_AGE_DAYS)
            if fresh < REFRESH_MIN_NEW_CLAIMS and not relations_changed and age < timedelta(days=REFRESH_MAX_AGE_DAYS):
                continue
        is_held = str(entity.get("ticker") or "").upper() in held
        try:
            recent_days = (now_dt.date() - datetime.fromisoformat(entity.get("last_seen") or "1970-01-01").date()).days
        except ValueError:
            recent_days = 999
        score = (min(entity.get("source_count", 0), 15) + 3 * min(len(relations), 6) + (12 if is_held else 0)
                 + (4 if any(r["state"] == "contested" for r in relations) else 0)
                 + (4 if recent_days <= 3 else 2 if recent_days <= 7 else 0) + (5 if not previous else 0))
        out.append({"entity": entity, "evidence": evidence, "relations": relations, "input_hash": input_hash,
                    "held": is_held, "score": score, "refresh": bool(previous)})
    out.sort(key=lambda item: (-item["score"], item["entity"]["id"]))
    return out


def build_insight_request(ontology: dict, candidate: dict) -> tuple[str, dict[str, dict]]:
    entity = candidate["entity"]
    source_label: dict[str, str] = {}
    keymap: dict[str, dict] = {}
    lines = []
    for row in candidate["evidence"]:
        label = source_label.setdefault(row["revision"], f"S{len(source_label) + 1}")
        key = f"k{len(keymap) + 1}"
        keymap[key] = row
        lines.append(f"- [{key}|{label}] ({row['day'] or '날짜 없음'} | {row.get('publisher') or '발행자 미상'} | "
                     f"{row['title'][:60]}) {row['text'][:500]}")
    relation_rows = "\n".join(
        f"- {wiki_ontology.relation_phrase(ontology, relation)} · {wiki_ontology.relation_label(relation)}"
        for relation in candidate["relations"][:14]) or "- (추출된 관계 없음)"
    voices = sorted({row.get("publisher") or "발행자 미상" for row in candidate["evidence"]})
    prompt = f"""You write one investment-wiki INSIGHT page in Korean. Return ONE JSON object only.

An insight is a judgment that no single claim states: it connects claims from DIFFERENT sources about the same
entity into a pattern, a causal chain, a contradiction, or a change over time, and says what follows for markets.
A restatement or summary of the claims is not an insight.

<entity>
{entity['name']} ({ENTITY_TYPES[entity['type']]}, {entity['id']})
</entity>{'''
This entity is a portfolio holding: prioritise what a holder must watch. Do not mention the holding in the text.''' if candidate['held'] else ''}
<ontology_relations>
{relation_rows}
</ontology_relations>
<publishers>
{", ".join(voices)}
</publishers>
<claims>
{chr(10).join(lines)}
</claims>

Rules:
- Claims are unverified model-compiled notes from public sources. Write "…로 전해졌다/보도됐다" style, never as confirmed fact.
- Each claim line names its publisher. Several posts by one publisher are one voice: when the claims you cite all
  come from a single publisher, say so in the text (for example "한 채널의 전언 기준") instead of implying agreement.
- A relation marked 견해·전망, and any claim from 증권사 리서치, is that party's outlook: attribute it ("○○증권은 …
  전망") and never restate it as fact. Give 엇갈림 relations and the opposing side the same weight as the supporting
  side — put them in "tensions" rather than leaving them out.
- "points": 2-5 judgments. Each cites at least 2 claim keys that come from at least 2 different sources (the S-label).
- "tensions": claims that disagree or pull in opposite directions (may be empty). Same citation rule.
- Use only facts and numbers that appear in the cited claims. No outside knowledge, no price targets, no buy/sell advice.
- "implications": 1-3 inferences for markets/sectors/holdings. These are your reasoning, so phrase them as conditional.
- "watch": 1-4 concrete observable triggers that would confirm or break the thesis.
- If the claims are unrelated trivia that support no cross-source judgment, return {{"action": "skip", "skip_reason": "..."}}.

JSON schema:
{{"action": "create", "title": "<max 60 chars, no entity name prefix needed>", "thesis": "<one or two sentences>",
  "points": [{{"text": "...", "claims": ["k1", "k4"]}}], "tensions": [{{"text": "...", "claims": ["k2", "k9"]}}],
  "implications": ["..."], "watch": ["..."], "confidence": 0.0, "horizon": "days|weeks|months"}}"""
    return prompt, keymap


def _numbers(text: object) -> set[str]:
    return {match.replace(",", "") for match in _NUMBER.findall(str(text or ""))}


def validate_insight(payload: object, keymap: dict[str, dict], *, entity: dict) -> tuple[dict | None, str]:
    """교차 출처 인용과 숫자 근거를 확인한다. 통과하지 못하면 (None, 사유)."""
    if not isinstance(payload, dict):
        return None, "payload_invalid"
    if payload.get("action") == "skip":
        return None, "model_skip"
    thesis = " ".join(str(payload.get("thesis") or "").split())
    title = " ".join(str(payload.get("title") or "").split())[:80]
    if payload.get("action") != "create" or len(thesis) < 20 or not title:
        return None, "shape_invalid"
    allowed_numbers = _numbers(" ".join([entity["name"], *entity.get("aliases", [])]))

    def cited(items: object, *, need: int) -> list[dict]:
        rows = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            text = " ".join(str(item.get("text") or "").split())
            keys = list(dict.fromkeys(key for key in item.get("claims") or [] if isinstance(key, str) and key in keymap))
            sources = {keymap[key]["revision"] for key in keys}
            grounded = allowed_numbers | _numbers(" ".join(keymap[key]["text"] for key in keys))
            if len(text) < 10 or len(keys) < 2 or len(sources) < need or not _numbers(text) <= grounded:
                continue
            rows.append({"text": text[:600], "claims": keys})
        return rows

    points = cited(payload.get("points"), need=2)[:5]
    if len(points) < 2:
        return None, "cross_source_points_missing"
    tensions = cited(payload.get("tensions"), need=1)[:3]
    used = [key for row in [*points, *tensions] for key in row["claims"]]
    every_number = allowed_numbers | _numbers(" ".join(keymap[key]["text"] for key in keymap))

    def texts(value: object) -> list[str]:
        return [" ".join(item.split())[:400] for item in value if isinstance(item, str) and item.strip()] \
            if isinstance(value, list) else []

    implications = [text for text in texts(payload.get("implications")) if _numbers(text) <= every_number][:3]
    if not _numbers(thesis) <= every_number:
        return None, "thesis_number_ungrounded"
    try:
        confidence = min(0.9, max(0.1, float(payload.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.4
    horizon = payload.get("horizon") if payload.get("horizon") in _HORIZONS else "weeks"
    return {"title": title, "thesis": thesis[:600], "points": points, "tensions": tensions,
            "implications": implications, "watch": texts(payload.get("watch"))[:4], "confidence": confidence,
            "horizon": horizon, "used_keys": list(dict.fromkeys(used))}, ""


def build_insight_page(ontology: dict, candidate: dict, insight: dict, keymap: dict[str, dict], *,
                       pages_by_id: dict[str, dict], generated_at: str) -> dict:
    entity = candidate["entity"]

    def refs(keys: list[str]) -> str:
        page_ids = list(dict.fromkeys(keymap[key]["page_id"] for key in keys))
        return ", ".join(wiki._document_wikilink(pages_by_id[page_id], str(pages_by_id[page_id].get("title") or page_id)[:60])
                         for page_id in page_ids if page_id in pages_by_id)

    body = ["## 핵심 판단", "", insight["thesis"], "", "## 근거를 이은 판단", ""]
    body += [f"{index}. {row['text']} (근거: {refs(row['claims'])})" for index, row in enumerate(insight["points"], 1)]
    if insight["tensions"]:
        body += ["", "## 엇갈리는 신호", ""] + [f"- {row['text']} (근거: {refs(row['claims'])})" for row in insight["tensions"]]
    if insight["implications"]:
        body += ["", "## 시사점 (모델 추론)", ""] + [f"- {text}" for text in insight["implications"]]
    if insight["watch"]:
        body += ["", "## 다음에 확인할 것", ""] + [f"- {text}" for text in insight["watch"]]
    if candidate["relations"]:
        body += ["", "## 온톨로지 관계", ""] + [
            f"- {wiki_ontology.relation_phrase(ontology, relation)} · {wiki_ontology.relation_label(relation)}"
            for relation in candidate["relations"][:10]]
    used_rows = [keymap[key] for key in insight["used_keys"]]
    page_ids = list(dict.fromkeys(row["page_id"] for row in used_rows))
    source_refs: list[str] = []
    for page_id in page_ids:
        for ref in (pages_by_id.get(page_id) or {}).get("source_refs") or []:
            if isinstance(ref, str) and ref.startswith("http") and ref not in source_refs:
                source_refs.append(ref)
    body += ["", f"_판단 시계: {_HORIZONS[insight['horizon']]} · 근거 claim {len(used_rows)}건 · "
                 f"출처 {len({row['revision'] for row in used_rows})}개 · "
                 f"발행자 {len({row.get('publisher') for row in used_rows})}곳 · 모델이 엮은 추론이며 원문 검증 전입니다._"]
    tags = ["insight", f"{ENTITY_TAG_PREFIX}{entity['id']}", f"entity_type:{entity['type']}"]
    if entity.get("ticker"):
        tags.append(f"ticker:{entity['ticker']}")
    return {
        "id": insight_page_id(entity["id"]), "title": f"{entity['name']} 인사이트: {insight['title']}"[:160],
        "summary": insight["thesis"], "body": "\n".join(body), "kind": INSIGHT_KIND, "surface": "market",
        "status": "draft", "tags": tags, "links": page_ids[:wiki.MAX_LINKS - 2], "source_refs": source_refs[:12],
        "confidence": insight["confidence"], "report_citation": insight["thesis"],
        "staleness_policy": "근거 claim 3건 이상 추가·관계 변경·7일 경과 시 재생성",
        "source_knowledge": {"insight": {
            "entity_id": entity["id"], "input_hash": candidate["input_hash"], "generated_at": generated_at,
            "evidence": [[row["page_id"], row["claim_id"]] for row in candidate["evidence"]],
            "cited": [[row["page_id"], row["claim_id"]] for row in used_rows],
            "relations": [relation["id"] for relation in candidate["relations"]],
            "horizon": insight["horizon"], "watch": insight["watch"], "held": candidate["held"],
        }},
    }


def write_insights(ontology: dict, pages: list[dict], *, llm_fn: LlmFn, limit: int, held: set[str] | None = None,
                   upsert: Callable[[dict], dict] | None = None, providers: dict[str, str] | None = None) -> dict:
    upsert = upsert or wiki.upsert_page
    pages_by_id = {str(page.get("id")): page for page in pages}
    candidates = insight_candidates(ontology, pages, held=held, providers=providers)
    summary = {"candidates": len(candidates), "calls": 0, "written": [], "rejected": {}}
    for candidate in candidates[:max(0, limit)]:
        prompt, keymap = build_insight_request(ontology, candidate)
        summary["calls"] += 1
        insight, reason = validate_insight(_reply(llm_fn(prompt))[0], keymap, entity=candidate["entity"])
        if insight is None:
            summary["rejected"][reason] = summary["rejected"].get(reason, 0) + 1
            continue
        saved = upsert(build_insight_page(ontology, candidate, insight, keymap, pages_by_id=pages_by_id,
                                          generated_at=_now()))
        summary["written"].append({"id": saved.get("id"), "entity": candidate["entity"]["id"],
                                   "title": saved.get("title"), "refresh": candidate["refresh"]})
    return summary


# ── 실행 ────────────────────────────────────────────────────────────────

def _held_tickers() -> set[str]:
    try:
        import portfolio_universe

        return {str(ticker).upper() for ticker in portfolio_universe.load_portfolio_tickers()}
    except Exception:
        return set()


def run(*, apply: bool = False, extract_calls: int | None = None, review_calls: int | None = None,
        insight_limit: int | None = None, llm_fn: LlmFn | None = None, review_llm_fn: ReviewFn | None = None,
        root: Path | None = None) -> dict:
    """``llm_fn`` 만 넘기면 그 함수가 검토자·되검토자 역할도 맡는다 (프롬프트로 구분)."""
    extract_calls = _int_env("WIKI_ONTOLOGY_EXTRACT_CALLS", 6) if extract_calls is None else extract_calls
    review_calls = _int_env("WIKI_ONTOLOGY_REVIEW_CALLS", 6) if review_calls is None else review_calls
    insight_limit = _int_env("WIKI_INSIGHT_MAX_PER_RUN", 3) if insight_limit is None else insight_limit
    injected = llm_fn

    def same_fn(prompt: str, barred: frozenset) -> object:
        return injected(prompt)

    review_llm_fn = review_llm_fn or (same_fn if injected else _default_review_llm)
    cross_llm_fn = same_fn if injected else _default_cross_llm
    llm_fn = llm_fn or _default_llm
    pages = wiki._all_wiki_pages()
    extractions = wiki_ontology.load_extractions(root)
    pending = pending_pages(pages, extractions)
    report: dict = {"apply": apply, "generated_at": _now(), "pending_pages": len(pending),
                    "pending_calls": len(batches(pending)),
                    "pending_review_calls": len(review_batches(pages, extractions)),
                    "pending_cross_review_calls": len(review_batches(pages, extractions, cross=True))}

    def save(data: dict) -> None:
        wiki_ontology.save_extractions(data, root)

    if apply:
        report["pruned"] = prune_extractions(pages, extractions)
        report["extraction"] = extract_relations(pages, extractions, llm_fn=llm_fn, max_calls=extract_calls, save=save)
        report["review"] = review_relations(pages, extractions, llm_fn=review_llm_fn, max_calls=review_calls,
                                            cross_llm_fn=cross_llm_fn, save=save)
        wiki_ontology.save_extractions(extractions, root)
    providers = source_providers()
    ontology = wiki_ontology.compile_ontology(pages, extractions, generated_at=report["generated_at"],
                                              source_providers=providers)
    report["ontology"] = ontology["stats"]
    held = _held_tickers()
    if apply:
        report["dir"] = str(wiki_ontology.save_ontology(ontology, root))
        report["insights"] = write_insights(ontology, pages, llm_fn=llm_fn, limit=insight_limit, held=held,
                                            providers=providers)
        if report["insights"]["written"]:
            wiki.rebuild_artifacts()
        wiki._append_wiki_log(
            "ontology", entities=ontology["stats"]["entities"], relations=ontology["stats"]["relations"],
            extracted_pages=report["extraction"]["pages"], insights=len(report["insights"]["written"]))
    else:
        report["insight_candidates"] = [
            {"entity": item["entity"]["id"], "score": item["score"], "claims": len(item["evidence"]),
             "relations": len(item["relations"]), "refresh": item["refresh"]}
            for item in insight_candidates(ontology, pages, held=held, providers=providers)[:15]]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="LLM 호출과 쓰기를 실행한다 (없으면 점검만)")
    parser.add_argument("--extract-calls", type=int, default=None, help="관계 추출 LLM 호출 상한")
    parser.add_argument("--review-calls", type=int, default=None, help="반대 검토 LLM 호출 상한")
    parser.add_argument("--insights", type=int, default=None, help="인사이트 작성 상한")
    args = parser.parse_args(argv)
    # Codex 호출은 따로 90초에서 끊긴다. 검토 프롬프트가 77초 걸린 실측(2026-11-16)이 있어, 이 잡에서만
    # PROVIDER_TIMEOUT 까지 기다리게 한다. 검토자가 Codex 하나뿐인 페이지는 시간 초과가 곧 검토 중단이다.
    os.environ.setdefault("AGENT_CONSOLE_CODEX_TIMEOUT", str(PROVIDER_TIMEOUT))
    print(json.dumps(run(apply=args.apply, extract_calls=args.extract_calls, review_calls=args.review_calls,
                         insight_limit=args.insights), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
