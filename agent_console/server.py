from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import agent, context, shared_memory, storage


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

    @app.post("/api/agent/chat")
    def agent_chat():
        payload = request.get_json(force=True)
        return jsonify(agent.answer(payload.get("message", ""), payload.get("surface", "market")))

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
