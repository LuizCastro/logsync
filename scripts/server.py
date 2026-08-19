#!/usr/bin/env python3
"""
Synapse — Dashboard API Server
Simple HTTP server that serves the Decision Board UI and provides REST API.

Usage:
    python3 server.py                  # rodar em http://localhost:8080
    python3 server.py --port 3000      # porta customizada
"""

import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
from decision_store import DecisionStore


class SynapseHandler(SimpleHTTPRequestHandler):
    """Serve dashboard + API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(os.path.dirname(__file__), "..", "dashboard"), **kwargs)

    @property
    def db_path(self):
        return os.environ.get("SYNAPSE_DB", "data/decisions.db")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/decisions":
            params = parse_qs(parsed.query)
            self._json_response(self._get_decisions(params))
        elif path == "/api/actions":
            self._json_response(self._get_actions())
        elif path == "/api/stats":
            self._json_response(self._get_stats())
        elif path == "/api/brief":
            self._json_response(self._get_brief())
        elif path == "/api/search":
            q = parse_qs(parsed.query).get("q", [""])[0]
            self._json_response(self._search(q))
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._json_response({"error": "invalid JSON"}, 400)
            return

        if parsed.path == "/api/decisions":
            self._json_response(self._insert_decision(data))
        elif parsed.path == "/api/actions":
            self._json_response(self._insert_action(data))
        elif parsed.path == "/api/actions/complete":
            self._json_response(self._complete_action(data))
        else:
            self._json_response({"error": "not found"}, 404)

    # ── API handlers ─────────────────────────────────────

    def _get_decisions(self, params=None):
        params = params or {}

        def _to_int(value, default):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        days = _to_int((params.get("days") or ["0"])[0], 0)
        limit = _to_int((params.get("limit") or ["1000"])[0], 1000)
        if limit <= 0:
            limit = 1000
        if limit > 5000:
            limit = 5000

        store = DecisionStore(self.db_path)
        decisions = store.get_recent_decisions(days=days, limit=limit)
        store.close()
        return decisions

    def _get_actions(self):
        store = DecisionStore(self.db_path)
        actions = store.get_actions()
        store.close()
        return actions

    def _get_stats(self):
        store = DecisionStore(self.db_path)
        stats = store.get_stats()
        store.close()
        return stats

    def _get_brief(self):
        store = DecisionStore(self.db_path)
        brief = store.generate_daily_brief()
        store.close()
        return brief

    def _search(self, query):
        store = DecisionStore(self.db_path)
        results = store.search_decisions(query)
        store.close()
        return results

    def _insert_decision(self, data):
        store = DecisionStore(self.db_path)
        decision_id = store.insert_decision(data)
        store.close()
        return {"id": decision_id, "status": "ok"}

    def _insert_action(self, data):
        store = DecisionStore(self.db_path)
        action_id = store.insert_action_item(data)
        store.close()
        return {"id": action_id, "status": "ok"}

    def _complete_action(self, data):
        store = DecisionStore(self.db_path)
        action_id = data.get("id")
        if action_id:
            store.complete_action(action_id)
        store.close()
        return {"status": "ok"}

    # ── Helpers ──────────────────────────────────────────

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        # Cleaner logs
        if "/api/" in str(args[0]):
            return  # Skip API request logs
        super().log_message(format, *args)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    default_db = os.environ.get("SYNAPSE_DB", "data/decisions.db")
    parser.add_argument("--db", default=default_db)
    args = parser.parse_args()

    os.environ["SYNAPSE_DB"] = args.db

    server = HTTPServer((args.host, args.port), SynapseHandler)
    print(f"🧠 Synapse API rodando em http://{args.host}:{args.port}")
    print(f"   Dashboard: http://localhost:{args.port}/")
    print(f"   API:       http://localhost:{args.port}/api/decisions")
    print(f"   Banco:     {args.db}")
    print()
    print("Endpoints:")
    print("  GET  /api/decisions      — listar decisões (query: ?days=0&limit=1000)")
    print("  GET  /api/actions        — action items pendentes")
    print("  GET  /api/stats          — estatísticas")
    print("  GET  /api/brief          — daily brief")
    print("  GET  /api/search?q=...   — buscar decisões")
    print("  POST /api/decisions      — criar decisão")
    print("  POST /api/actions        — criar action item")
    print("  POST /api/actions/complete — marcar como done")
    print()
    print("Ctrl+C pra parar")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Synapse encerrado.")
        server.server_close()


if __name__ == "__main__":
    main()
