#!/usr/bin/env python3
"""reports/wiki_distillation.py — source_digest 더미를 playbook/risk/concept 판단 카드로 증류.

문제: source_digest 는 reports/source_wiki_curator.py 가 규칙 기반으로 뉴스·데이터를
요약만 할 뿐 판단을 만들지 않는다. playbook/risk/decision/concept(재사용 가능한 판단)은
오직 대화(agent_console.wiki.auto_curate_from_chat)에서만 생기는데, 대화가 없는 날은
판단 카드가 전혀 안 쌓인다 — source_digest 만 83% 를 차지하는 편중의 원인.

이 크론은 주기적으로 아직 판단 카드에 링크되지 않은 source_digest 를 검토해,
승격할 만한 패턴이 있으면 draft 상태의 playbook/risk/concept 후보를 만들어 원본
digest 에 링크한다 (source_digest 자체는 건드리지 않음 — 새 카드만 추가).
확신 낮은 draft 라 후속 위키 헬스체크·사람 리뷰로 승격 여부가 갈린다.

사용법:
    uv run python -m reports.wiki_distillation --dry-run
    uv run python -m reports.wiki_distillation
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_console import wiki

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MAX_CANDIDATES_PER_RUN = 5
_JUDGMENT_KINDS = ("playbook", "risk", "decision", "concept")
_DISTILLABLE_KINDS = ("playbook", "risk", "concept")


def _has_judgment_link(page: dict) -> bool:
    """이 source_digest 가 이미 playbook/risk/decision/concept 카드로 연결돼 있는가."""
    for link_id in [*(page.get("links") or []), *(page.get("backlinks") or [])]:
        linked = wiki.get_page(link_id)
        if linked and linked.get("kind") in _JUDGMENT_KINDS:
            return True
    return False


def select_distillation_candidates(pages: list[dict], *, limit: int = MAX_CANDIDATES_PER_RUN) -> list[dict]:
    """판단 카드로 아직 안 이어진 source_digest 중 근거(evidence)가 많은 순 상위 N개."""
    unlinked = [
        p for p in pages
        if p.get("kind") == "source_digest"
        and p.get("status") != "archived"
        and not _has_judgment_link(p)
    ]
    unlinked.sort(key=lambda p: len(p.get("evidence_ids") or []), reverse=True)
    return unlinked[:limit]


def _build_distillation_prompt(page: dict) -> str:
    return "\n".join([
        "너는 stock-report AI 위키 증류기다.",
        "아래는 규칙 기반으로 자동 수집된 소스 다이제스트 1건이다.",
        "이 다이제스트에서 재사용 가능한 판단(전략/위험 요인/개념 정의)을 뽑을 수 있으면",
        "카드 하나를 만들고, 단순 사실 나열이라 아직 판단으로 승격할 게 없으면 skip 한다.",
        "kind 는 playbook(재사용 가능한 전략/절차) · risk(손실·MDD 등 위험 요인) ·",
        "concept(용어/지표/구조 정의) 중 내용에 맞는 것 하나만 고른다.",
        "확신이 낮으면 status 는 draft 로 한다 (기본값이자 권장값).",
        "반드시 JSON object만 출력한다. 마크다운, 설명문, 코드펜스는 금지한다.",
        '{"action":"create","kind":"playbook|risk|concept","title":"...","summary":"...",'
        '"body":"...","status":"draft","confidence":0.0,"reason":"..."}',
        '판단으로 승격할 게 없으면: {"action":"skip","reason":"..."}',
        "",
        f"[다이제스트: {page.get('title', '')}]",
        f"요약: {page.get('summary', '')}",
        f"본문: {(page.get('body') or '')[:3000]}",
    ])


def _distill_one(page: dict, llm_fn) -> dict | None:
    prompt = _build_distillation_prompt(page)
    try:
        text = llm_fn(prompt)
    except Exception as e:
        logger.warning("증류 LLM 호출 실패 (%s): %s", page.get("id"), e)
        return None
    plan = wiki._parse_curation_plan(text)
    if not plan or str(plan.get("action", "")).lower() != "create":
        return None
    kind = str(plan.get("kind", "")).lower()
    if kind not in _DISTILLABLE_KINDS:
        return None
    payload = wiki._plan_to_page_payload(
        plan,
        question=f"[자동증류] {page.get('title', '')}",
        answer=plan.get("body") or "",
        surface=page.get("surface", "market"),
    )
    if not payload:
        return None
    payload["links"] = wiki._clean_links(
        [page.get("id"), *(payload.get("links") or [])], self_id=payload["id"]
    )
    payload["source_refs"] = wiki._dedupe_texts(
        [*(payload.get("source_refs") or []), f"wiki:{page.get('id')}"], limit=12, item_limit=180
    )
    return payload


def run(*, dry_run: bool = False, llm_fn=None) -> dict:
    if llm_fn is None:
        from agent_console.agent import _try_llm_prompt as llm_fn

    pages = wiki.list_pages(status="all", limit=500)
    candidates = select_distillation_candidates(pages)
    created = []
    for page in candidates:
        payload = _distill_one(page, llm_fn)
        if not payload:
            continue
        if dry_run:
            created.append(payload)
        else:
            saved = wiki.upsert_page(payload)
            created.append(saved)
    if created and not dry_run:
        wiki.rebuild_artifacts()
    return {"dry_run": dry_run, "candidates_considered": len(candidates), "created": created}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run)
    logger.info(
        "위키 증류 완료: 후보 %d건 검토, %d건 생성 (dry_run=%s)",
        result["candidates_considered"], len(result["created"]), result["dry_run"],
    )
    for page in result["created"]:
        logger.info("  + [%s] %s", page.get("kind"), page.get("title"))

    if result["created"] and not args.dry_run:
        try:
            import notify
            lines = [f"🧬 위키 증류: {len(result['created'])}건 draft 생성"]
            for page in result["created"]:
                lines.append(f"- [{page.get('kind')}] {page.get('title')}")
            notify.send_telegram(
                "\n".join(lines), token=os.getenv("STOCK_BOT_TOKEN"),
                chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15,
            )
        except Exception as e:
            logger.warning("텔레그램 발송 실패: %s", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
