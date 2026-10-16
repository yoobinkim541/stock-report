from __future__ import annotations

"""Legacy history/routing compatibility for the current console engine.

This module is imported automatically by Python when present on sys.path.
It merges recent history across surfaces without replacing the answer pipeline.
"""

from collections.abc import Iterable

try:
    from agent_console import agent, storage
except Exception:  # pragma: no cover - safe no-op when optional deps are unavailable
    agent = None
else:
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
        if len(q) <= 20 and default != "market":
            return default
        if len(q) <= 20 and recent_surface:
            return recent_surface
        return "market"

    agent.recent_conversation_history = _recent_conversation_history
    agent.infer_surface = _patched_infer_surface
    agent._safe_list_conversation = lambda limit, surface: _recent_conversation_history(surface, limit)
