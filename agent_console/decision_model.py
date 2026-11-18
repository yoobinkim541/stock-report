"""Wiki candidate reranking with TypeSafe Jev through OpenRouter's Decisions API.

Jev answers typed judgments with probabilities and writes no text. Each candidate page
gets one yes/no question, "does this page directly help answer the question?", and
the candidates are ordered by that probability. On 16 real chat questions
(2026-11-18) the top 4 chosen this way scored 100 against 81 for the retrieval order
and 108 for the best possible 4, at about $0.0007 and 0.4 s per question.

Enabled when OPENROUTER_API_KEY is set; AGENT_CONSOLE_WIKI_RERANK=0 turns it off.
Any failed or malformed answer keeps the retrieval order: ranking is a relevance
hint, never a fact check.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
# The endpoint is alpha: pin the build whose probabilities the evaluation measured.
MODEL = "typesafe/jev-1.14"
REQUEST_TIMEOUT_SECONDS = 4.0
MAX_PAGES = 20
_KST = timezone(timedelta(hours=9))
_CITATION = re.compile(r"\[S\d+\]\([^)]*\)|https?://\S+")


def enabled() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip()) and os.getenv("AGENT_CONSOLE_WIKI_RERANK", "1").strip() != "0"


def _post(payload: dict) -> dict:
    request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode("utf-8"), headers={
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY'].strip()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def _card(page: dict) -> dict:
    body = " ".join(_CITATION.sub("", str(page.get("body") or "")).split())
    return {"title": str(page.get("title") or ""), "kind": page.get("kind"),
            "updated": str(page.get("updated_at") or "")[:10],
            "summary": str(page.get("summary") or "")[:300], "excerpt": body[:600]}


def _helps(question: str, page: dict, today: str) -> float:
    answer = _post({"model": MODEL, "state": {"user_question": question, "wiki_page": _card(page)}, "questions": {
        "helps": {"type": "noul",
                  "instructions": f"Today is {today}. Does this wiki page contain information that directly helps "
                                  "answer the user's question?",
                  "criteria": {"true": "The page states facts, data or views that answer part of the question.",
                               "false": "The page is unrelated or only shares a word with the question."}}}})
    value = (answer.get("answers") or {}).get("helps") or {}
    if value.get("type") != "noul" or not isinstance(value.get("noul"), (int, float)):
        raise ValueError("malformed decision")
    return float(value["noul"])


def rank_wiki_pages(question: str, pages: list[dict]) -> tuple[list[dict], dict]:
    """Return ``pages`` ordered by Jev's probability that each helps, plus a status record."""
    pages = list(pages or [])
    if not enabled():
        return pages, {"method": "retrieval", "reason": "disabled"}
    if len(pages) < 2:
        return pages, {"method": "retrieval", "reason": "too_few_pages"}
    head, tail = pages[:MAX_PAGES], pages[MAX_PAGES:]
    today = datetime.now(_KST).date().isoformat()
    try:
        with ThreadPoolExecutor(max_workers=len(head)) as pool:
            scores = list(pool.map(lambda page: _helps(question, page, today), head))
    except (OSError, ValueError, KeyError) as exc:
        return pages, {"method": "retrieval", "reason": f"decision_failed:{type(exc).__name__}"}
    order = sorted(range(len(head)), key=lambda index: -scores[index])
    return [head[index] for index in order] + tail, {
        "method": "jev", "model": MODEL,
        "scores": {str(head[index].get("id")): round(scores[index], 3) for index in order}}
