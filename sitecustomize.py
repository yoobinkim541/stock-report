from __future__ import annotations

"""Runtime patch for stock-report console context handling.

This module is imported automatically by Python when present on sys.path.
It keeps the existing engine intact, but merges recent conversation history
across surfaces and makes short follow-ups easier to route.
"""

import os
from collections.abc import Iterable
from datetime import datetime

try:
    from agent_console import agent, context, shared_memory, storage, wiki
except Exception:  # pragma: no cover - safe no-op when optional deps are unavailable
    agent = None
else:
    def _safe_context_pack(surface: str) -> dict:
        try:
            return context.context_pack(surface)
        except Exception as exc:
            try:
                focus = context.focus_for_surface(surface)
            except Exception:
                focus = []
            return {
                "ok": False,
                "surface": str(surface or "market").strip().lower(),
                "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
                "project": "stock-report",
                "sources": {"events": [], "source_counts": [], "symbol_counts": []},
                "reports": [],
                "ml_activity": [],
                "portfolio": {"holdings": [], "summary": {}, "risk": {}, "targets": {}, "errors": [str(exc)]},
                "paper": {"kr": None, "us": None, "combined": None, "errors": [str(exc)]},
                "models": {"items": []},
                "memory": [],
                "focus": focus,
                "shared_memory": {"ok": False, "error": str(exc), "records": []},
                "context_error": str(exc),
            }

    def _recent_conversation_history(surface: str, limit: int = 12) -> list[dict]:
        surface = str(surface or "market").strip().lower()
        limit = max(1, min(int(limit or 12), 50))
        merged: list[dict] = []
        seen: set[str] = set()

        def _push(rows: Iterable[dict]) -> None:
            for row in rows:
                key = str(row.get("id") or row.get("created_at") or row.get("message") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(row)

        try:
            _push(storage.list_conversation(limit=limit, context_surface=surface))
            _push(storage.list_conversation(limit=limit))
        except Exception:
            return []

        merged.sort(key=lambda row: (str(row.get("created_at") or ""), int(row.get("id") or 0)))
        return merged[-limit:]

    def _most_recent_surface(history: list[dict] | None) -> str:
        for row in reversed(history or []):
            surface = str(row.get("context_surface") or row.get("surface") or "").strip().lower()
            if surface in getattr(agent, "_SURFACE_TITLES", {}):
                return surface
        return ""

    def _patched_infer_surface(question: str, history: list[dict] | None = None,
                               default: str = "market") -> str:
        default = str(default or "market").strip().lower()
        if default not in getattr(agent, "_SURFACE_TITLES", {}):
            default = "market"
        q = str(question or "").strip().lower()
        if not q:
            return default

        if not history:
            history = _recent_conversation_history(default, limit=8)
        recent_surface = _most_recent_surface(history)

        best_surface, best_score = "", 0
        for surface, words in getattr(agent, "_SURFACE_ROUTE_HINTS", ()):  # type: ignore[attr-defined]
            score = sum(1 for w in words if w in q)
            if score > best_score:
                best_surface, best_score = surface, score
        if best_score > 0:
            return best_surface

        try:
            if agent._extract_asset_symbol(question):
                return "ticker"
        except Exception:
            pass

        followup_words = ("아니", "아니아니", "그거 말고", "말고", "정정", "다시", "아니고", "그게 아니라")
        if recent_surface and any(word in q for word in followup_words):
            return recent_surface
        if len(q) <= 20 and recent_surface:
            return recent_surface
        if len(q) <= 20 and default != "market":
            return default
        return "market"

    def _humanize_generic_fallback(question: str, surface: str, history: list[dict], response: str) -> str:
        previous = ""
        try:
            previous = agent._last_user_question(history)
        except Exception:
            previous = ""
        if previous and len(question) <= 28 and response.startswith("질문은 이해했습니다:"):
            return (
                f"방금 말은 **“{previous}”**의 후속으로 이해했습니다.\n\n"
                f"{response}\n\n"
                "지금은 모델 응답이 잠깐 비어 있어서 규칙 기반으로 짧게 답했지만, 이어지는 문맥은 같이 보존했습니다."
            )
        if surface == "portfolio" and previous and len(question) <= 28 and "질문은 이해했습니다:" in response:
            return (
                f"방금 말은 **“{previous}”**에 이어진 질문으로 읽었습니다.\n\n"
                f"{response}"
            )
        return response

    def _patched_answer(question: str, surface: str = "market") -> dict:
        question = str(question or "").strip()
        surface = str(surface or "market").strip().lower()
        if not question:
            return {"ok": False, "error": "질문을 입력해 주세요."}

        history = _recent_conversation_history(surface, limit=12)
        try:
            storage.add_conversation("user", question, surface)
        except Exception:
            pass
        pack = _safe_context_pack(surface)
        agent._reset_llm_engine()
        try:
            response = agent._compose_answer(question, pack, history=history)
        except Exception as exc:
            response = agent._compose_error_fallback_answer(question, pack, exc)
        engine = getattr(agent, "_LAST_LLM_ENGINE", None) or "local-rules"
        response = _humanize_generic_fallback(question, surface, history, response)
        try:
            storage.add_conversation("assistant", response, surface)
        except Exception:
            pass
        try:
            shared_memory.append_chat_exchange(question, response, surface)
        except Exception:
            pass
        try:
            if os.getenv("AGENT_CONSOLE_WIKI_AUTOCURATE_ENABLED", "1").lower() not in {"0", "false", "no", "off"}:
                wiki.auto_curate_from_chat(
                    question,
                    response,
                    surface=surface,
                    pack=pack,
                    history=history,
                    llm=agent._try_llm_prompt,
                )
        except Exception:
            pass
        sources = pack.get("sources") or {}
        return {
            "ok": True,
            "answer": response,
            "surface": surface,
            "context": {
                "engine": engine,
                "event_count": len(sources.get("events") or []),
                "memory_count": len(pack.get("memory") or []),
                "shared_memory_count": (pack.get("shared_memory") or {}).get("recordCount", 0),
                "source_counts": sources.get("source_counts") or [],
                "symbol_counts": sources.get("symbol_counts") or [],
                "context_error": pack.get("context_error"),
            },
            "conversation": _recent_conversation_history(surface, limit=20),
        }

    agent.recent_conversation_history = _recent_conversation_history
    agent.infer_surface = _patched_infer_surface
    agent.answer = _patched_answer
