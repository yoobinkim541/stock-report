from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from . import agent, context, shared_memory, storage, wiki


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
    storage.ensure_schema()

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/health")
    def health():
        return jsonify({
            "ok": True,
            "app": "stock-report-agent-console",
            "db": str(storage.db_path()),
            "reports_dir": str(context.reports_dir()),
        })

    @app.get("/api/context/overview")
    def context_overview():
        surface = request.args.get("surface", "market")
        hours = int(request.args.get("hours", "72") or 72)
        return jsonify(context.context_pack(surface, hours=hours))

    @app.post("/api/memory/ingest")
    def memory_ingest():
        payload = request.get_json(silent=True) or {}
        hours = int(payload.get("hours") or request.args.get("hours") or 72)
        return jsonify(context.ingest_recent_memory(hours=hours))

    @app.get("/api/memory")
    def shared_memory_status():
        limit = int(request.args.get("limit", "8") or 8)
        offset = int(request.args.get("offset", "0") or 0)
        return jsonify(shared_memory.status(limit=limit, offset=offset))

    @app.post("/api/memory")
    def shared_memory_add():
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "memory record object required"}), 400
        return jsonify({"ok": True, "record": shared_memory.append_record(payload)})

    @app.post("/api/memory/context")
    def shared_memory_context():
        payload = request.get_json(silent=True) or {}
        return jsonify(shared_memory.build_context_packet(payload))

    @app.delete("/api/memory")
    def shared_memory_delete():
        record_id = request.args.get("id", "")
        deleted = shared_memory.delete_record(record_id)
        return jsonify({"ok": deleted, "deleted": deleted})

    @app.get("/api/memory/events")
    def memory_events():
        limit = int(request.args.get("limit", "80") or 80)
        return jsonify({"ok": True, "events": storage.list_memory_events(limit=limit)})

    @app.post("/api/memory/events")
    def memory_add():
        payload = request.get_json(force=True)
        event = payload.get("event") if isinstance(payload, dict) else payload
        if not isinstance(event, dict):
            return jsonify({"ok": False, "error": "event object required"}), 400
        changed = storage.upsert_memory_events([event])
        return jsonify({"ok": True, "changed": changed})

    @app.get("/api/wiki/pages")
    def wiki_pages():
        query = request.args.get("query", "")
        surface = request.args.get("surface", "all")
        status = request.args.get("status", "all")
        limit = int(request.args.get("limit", "20") or 20)
        return jsonify({
            "ok": True,
            "pages": wiki.list_pages(query=query, surface=surface, status=status, limit=limit),
            "stats": wiki.stats(),
        })

    @app.get("/api/wiki/pages/<page_id>")
    def wiki_page_get(page_id: str):
        page = wiki.get_page(page_id)
        if not page:
            return jsonify({"ok": False, "error": "page not found"}), 404
        return jsonify({"ok": True, "page": page})

    @app.post("/api/wiki/pages")
    def wiki_page_upsert():
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "page object required"}), 400
        return jsonify({"ok": True, "page": wiki.upsert_page(payload)})

    @app.post("/api/wiki/capture")
    def wiki_capture():
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "payload object required"}), 400
        page = wiki.capture_from_chat(
            payload.get("question", ""),
            payload.get("answer", ""),
            surface=payload.get("surface", "market"),
            title=payload.get("title"),
            status=payload.get("status", "draft"),
            kind=payload.get("kind", "playbook"),
            tags=payload.get("tags") or [],
            source_refs=payload.get("source_refs") or [],
        )
        return jsonify({"ok": True, "page": page})

    @app.delete("/api/wiki/pages/<page_id>")
    def wiki_page_delete(page_id: str):
        deleted = wiki.delete_page(page_id)
        if not deleted:
            return jsonify({"ok": False, "error": "page not found"}), 404
        return jsonify({"ok": True, "deleted": True})

    @app.post("/api/agent/chat")
    def agent_chat():
        payload = request.get_json(force=True)
        return jsonify(agent.answer(
            payload.get("message", ""),
            payload.get("surface", "market"),
            async_postprocess=True,
        ))

    @app.post("/api/agent/chat/stream")
    def agent_chat_stream():
        payload = request.get_json(force=True)
        message = payload.get("message", "")
        surface = payload.get("surface", "market")

        def frame(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        @stream_with_context
        def generate():
            yield frame("stage", {"label": "맥락 읽는 중", "detail": "최근 대화와 화면 컨텍스트를 확인하고 있습니다."})
            try:
                result = agent.answer(message, surface, async_postprocess=True)
                yield frame("answer", result)
            except Exception as exc:
                yield frame("answer", {"ok": False, "error": str(exc)})

        return Response(generate(), mimetype="text/event-stream")

    @app.get("/api/agent/context-prompt")
    def context_prompt():
        surface = request.args.get("surface", "market")
        return jsonify({"ok": True, "prompt": agent.build_context_prompt(surface)})

    @app.get("/api/portfolio-lab/scenarios")
    def scenarios():
        return jsonify({"ok": True, "scenarios": storage.list_scenarios()})

    @app.post("/api/portfolio-lab/scenarios")
    def scenario_save():
        payload = request.get_json(force=True)
        return jsonify({"ok": True, "scenario": storage.save_scenario(payload)})

    @app.get("/api/local-install-prompt")
    def local_install_prompt():
        path = Path(__file__).resolve().parent.parent / "docs" / "local-agent-console-install-prompt.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return jsonify({"ok": True, "prompt": text})

    return app


def main() -> int:
    host = os.getenv("AGENT_CONSOLE_HOST", "127.0.0.1")
    port = int(os.getenv("AGENT_CONSOLE_PORT", "8797"))
    app = create_app()
    app.run(host=host, port=port, debug=os.getenv("AGENT_CONSOLE_DEBUG", "0") == "1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
