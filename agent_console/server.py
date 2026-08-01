from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import agent, chart_alerts, context, shared_memory, storage, strategy_studio, wiki


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
    storage.ensure_schema()

    @app.after_request
    def add_cors_headers(response):
        origin = str(request.headers.get("Origin") or "").strip()
        allowed = {
            item.strip().rstrip("/")
            for item in str(os.getenv("AGENT_CONSOLE_CORS_ORIGINS") or "http://localhost:8501,http://127.0.0.1:8501").split(",")
            if item.strip()
        }
        if origin and origin.rstrip("/") in allowed:
            response.headers.setdefault("Access-Control-Allow-Origin", origin)
            response.headers.setdefault("Vary", "Origin")
            response.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            response.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
        return response

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

    @app.get("/api/strategy-studio/specs")
    def strategy_specs():
        limit = int(request.args.get("limit", "50") or 50)
        return jsonify({"ok": True, "specs": strategy_studio.list_strategy_specs(limit=limit)})

    @app.post("/api/strategy-studio/specs")
    def strategy_spec_save():
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "spec object required"}), 400
        return jsonify({"ok": True, "spec": strategy_studio.save_strategy_spec(payload)})

    @app.get("/api/strategy-studio/specs/<spec_id>")
    def strategy_spec_get(spec_id: str):
        version = request.args.get("version")
        spec = strategy_studio.get_strategy_spec(spec_id, version=int(version) if version else None)
        if not spec:
            return jsonify({"ok": False, "error": "strategy spec not found"}), 404
        return jsonify({"ok": True, "spec": spec})

    @app.get("/api/strategy-studio/specs/<spec_id>/versions")
    def strategy_spec_versions(spec_id: str):
        limit = int(request.args.get("limit", "20") or 20)
        return jsonify({"ok": True, "versions": strategy_studio.list_strategy_versions(spec_id, limit=limit)})

    @app.post("/api/strategy-studio/specs/<spec_id>/preview")
    def strategy_spec_preview(spec_id: str):
        payload = request.get_json(silent=True) or {}
        spec = strategy_studio.get_strategy_spec(spec_id, version=int(payload.get("version")) if payload.get("version") else None)
        if not spec:
            return jsonify({"ok": False, "error": "strategy spec not found"}), 404
        benchmark = payload.get("benchmark") or payload.get("benchmark_symbol")
        period = payload.get("period")
        return jsonify(strategy_studio.preview_strategy_spec(spec.get("spec") or spec, benchmark=benchmark, period=period))

    @app.post("/api/strategy-studio/specs/<spec_id>/patch-preview")
    def strategy_spec_patch_preview(spec_id: str):
        payload = request.get_json(silent=True) or {}
        spec = strategy_studio.get_strategy_spec(spec_id)
        if not spec:
            return jsonify({"ok": False, "error": "strategy spec not found"}), 404
        question = str(payload.get("question") or "").strip()
        history = payload.get("history") if isinstance(payload.get("history"), list) else []
        pack = payload.get("pack") if isinstance(payload.get("pack"), dict) else {}
        return jsonify(strategy_studio.propose_strategy_patch(question, spec.get("spec") or spec, history=history, pack=pack))

    @app.get("/api/chart-workspaces/<workspace_id>/drawings")
    def chart_workspace_drawing_get(workspace_id: str):
        store_key = str(request.args.get("store_key") or "").strip()
        if not store_key:
            return jsonify({"ok": False, "error": "store_key is required"}), 400
        snapshot = storage.get_chart_drawing_snapshot(workspace_id, store_key)
        if not snapshot:
            return jsonify({"ok": False, "error": "drawing snapshot not found", "snapshot": None}), 404
        return jsonify({"ok": True, "snapshot": snapshot})

    @app.get("/api/chart-workspaces/<workspace_id>/drawings/list")
    def chart_workspace_drawing_list(workspace_id: str):
        limit = int(request.args.get("limit", "50") or 50)
        return jsonify({
            "ok": True,
            "snapshots": storage.list_chart_drawing_snapshots(workspace_id, limit=limit),
        })

    @app.post("/api/chart-workspaces/<workspace_id>/drawings")
    def chart_workspace_drawing_save(workspace_id: str):
        payload = request.get_json(silent=True) or {}
        store_key = str(payload.get("store_key") or "").strip()
        drawing = payload.get("drawing")
        if not store_key:
            return jsonify({"ok": False, "error": "store_key is required"}), 400
        if not isinstance(drawing, dict):
            return jsonify({"ok": False, "error": "drawing object required"}), 400
        try:
            snapshot = storage.save_chart_drawing_snapshot(
                workspace_id,
                store_key,
                drawing,
                source=str(payload.get("source") or "browser"),
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "snapshot": snapshot})

    @app.get("/api/chart-alerts/rules")
    def chart_alert_rules():
        limit = int(request.args.get("limit", "50") or 50)
        workspace_id = request.args.get("workspace_id")
        symbol = request.args.get("symbol")
        enabled_arg = request.args.get("enabled")
        enabled = None
        if enabled_arg is not None:
            enabled = str(enabled_arg).strip().lower() not in {"0", "false", "no", "off"}
        return jsonify({
            "ok": True,
            "rules": storage.list_chart_alert_rules(
                workspace_id=workspace_id,
                symbol=symbol,
                enabled=enabled,
                limit=limit,
            ),
        })

    @app.post("/api/chart-alerts/rules")
    def chart_alert_rule_save():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "alert rule object required"}), 400
        try:
            rule = storage.save_chart_alert_rule(payload)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "rule": rule})

    @app.post("/api/chart-alerts/rules/<rule_id>/evaluate")
    def chart_alert_rule_evaluate(rule_id: str):
        payload = request.get_json(silent=True) or {}
        rule = storage.get_chart_alert_rule(rule_id)
        if not rule:
            return jsonify({"ok": False, "error": "chart alert rule not found"}), 404
        result = chart_alerts.evaluate_price_alert(
            rule,
            previous_price=payload.get("previous_price"),
            current_price=payload.get("current_price"),
            as_of=payload.get("as_of"),
        )
        if result.get("triggered"):
            storage.update_chart_alert_state(rule_id, {
                "triggered": True,
                "event": result.get("event"),
                "last_price": payload.get("current_price"),
                "last_checked_at": payload.get("as_of"),
            })
        return jsonify({"ok": True, **result})

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
