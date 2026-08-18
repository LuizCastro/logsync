#!/usr/bin/env python3
"""
Synapse — OCI Decision Store with RAG
Uses OCI Generative AI Agent Knowledge Base for semantic retrieval
of past decisions, plus SQLite for structured storage.

Architecture:
  - OCI Knowledge Base: stores decision docs for semantic search (RAG)
  - SQLite: structured storage for decisions + action items
  - n8n: orchestrates the pipeline
"""

import oci
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List


class OCIDecisionStore:
    """
    Hybrid store: SQLite for structured data, OCI KB for semantic search.
    When a new decision comes in:
      1. Save to SQLite (structured)
      2. Upload to OCI Object Storage → KB ingestion (semantic)
    When querying:
      1. Semantic search via OCI Agent endpoint (RAG)
      2. Structured queries via SQLite
    """

    def __init__(self, db_path: str = "data/decisions.db", oci_config_path: str = "data/oci-config.json"):
        # SQLite
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

        # OCI
        self.oci_config = self._load_oci_config(oci_config_path)
        if self.oci_config:
            config = oci.config.from_file()
            config["region"] = self.oci_config["oci_region"]
            self.agent_client = oci.generative_ai_agent_runtime.GenerativeAiAgentRuntimeClient(config)
            self.agent_endpoint_id = self.oci_config["agent_endpoint_id"]
            self._session_id = None

    def _load_oci_config(self, path):
        try:
            with open(path) as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"⚠️  OCI config not found at {path}. RAG features disabled.")
            return None

    def _init_schema(self):
        schema_path = Path(__file__).parent / "schema.sql"
        with open(schema_path) as f:
            self.conn.executescript(f.read())

    # ── Save Decision (dual-write: SQLite + OCI) ──────────────

    def save_decision(self, decision: dict) -> str:
        """Save a decision to both SQLite and OCI KB."""
        decision_id = decision.get("id") or self._generate_id()
        decision["id"] = decision_id

        # 1. Save to SQLite
        self._save_to_sqlite(decision)

        # 2. Upload to OCI KB for semantic search
        self._upload_to_oci_kb(decision)

        return decision_id

    def _save_to_sqlite(self, d: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO decisions
               (id, title, decision, rationale, alternatives_rejected,
                owner, action_items, confidence, related_topics,
                source, meeting_id, channel, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                d["id"], d["title"], d["decision"], d.get("rationale"),
                json.dumps(d.get("alternatives_rejected", [])),
                d.get("owner"), json.dumps(d.get("action_items", [])),
                d.get("confidence", 0.5), json.dumps(d.get("related_topics", [])),
                d.get("source", "meeting"), d.get("meeting_id"), d.get("channel"),
                d.get("created_at", datetime.now().isoformat()),
            ),
        )
        self.conn.commit()

    def _upload_to_oci_kb(self, decision: dict):
        """Upload decision as a document to OCI Object Storage for KB ingestion."""
        if not self.oci_config:
            return

        try:
            # Create a document from the decision
            doc_content = {
                "id": decision["id"],
                "title": decision["title"],
                "decision": decision["decision"],
                "rationale": decision.get("rationale", ""),
                "alternatives": decision.get("alternatives_rejected", []),
                "owner": decision.get("owner", ""),
                "action_items": decision.get("action_items", []),
                "topics": decision.get("related_topics", []),
                "source": decision.get("source", "meeting"),
                "meeting_id": decision.get("meeting_id", ""),
                "date": decision.get("created_at", datetime.now().isoformat()),
            }

            # Upload to Object Storage (the KB ingests from here)
            # This would use oci.object_storage.ObjectStorageClient
            # For now, we store locally and rely on the n8n pipeline
            doc_path = Path(f"data/oci-docs/{decision['id']}.json")
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            with open(doc_path, "w") as f:
                json.dump(doc_content, f, indent=2, ensure_ascii=False)

        except Exception as e:
            print(f"⚠️  Failed to upload to OCI KB: {e}")

    # ── Semantic Search via OCI Agent (RAG) ───────────────────

    def search_decisions_rag(self, query: str, max_results: int = 5) -> List[dict]:
        """
        Use OCI Agent's RAG capability to find related past decisions.
        The agent searches its Knowledge Base semantically.
        """
        if not self.oci_config or not self.agent_client:
            # Fallback to SQLite keyword search
            return self.search_decisions_sqlite(query, max_results)

        try:
            # Create or reuse session
            if not self._session_id:
                session_response = self.agent_client.create_session(
                    create_session_details=oci.generative_ai_agent_runtime.models.CreateSessionDetails(
                        agent_id=self.oci_config.get("agent_id", ""),
                    )
                )
                self._session_id = session_response.data.id

            # Chat with the agent using RAG
            search_prompt = f"""Search the Knowledge Base for decisions related to: "{query}"

Return the most relevant decisions as a JSON array. Each decision should have:
- title, decision, rationale, owner, date, source

If no related decisions are found, return an empty array."""

            chat_response = self.agent_client.chat(
                chat_details=oci.generative_ai_agent_runtime.models.ChatDetails(
                    agent_endpoint_id=self.agent_endpoint_id,
                    session_id=self._session_id,
                    user_message=search_prompt,
                )
            )

            # Parse response
            response_text = chat_response.data.message.content
            try:
                results = json.loads(response_text)
                if isinstance(results, list):
                    return results[:max_results]
                elif isinstance(results, dict) and "decisions" in results:
                    return results["decisions"][:max_results]
            except json.JSONDecodeError:
                # If LLM returns text, wrap it
                return [{"decision": response_text, "source": "oci_rag"}]

        except Exception as e:
            print(f"⚠️  OCI RAG search failed: {e}, falling back to SQLite")
            return self.search_decisions_sqlite(query, max_results)

    def search_decisions_sqlite(self, keyword: str, limit: int = 20) -> List[dict]:
        """Fallback: keyword search in SQLite."""
        rows = self.conn.execute(
            """SELECT * FROM decisions
               WHERE decision LIKE ? OR title LIKE ? OR rationale LIKE ?
               ORDER BY confidence DESC LIMIT ?""",
            (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── Structured Queries (SQLite) ───────────────────────────

    def get_decision(self, decision_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_recent_decisions(self, days: int = 7, limit: int = 50) -> List[dict]:
        rows = self.conn.execute(
            """SELECT * FROM decisions
               WHERE created_at > datetime('now', ?)
               ORDER BY created_at DESC LIMIT ?""",
            (f"-{days} days", limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def insert_action_item(self, action: dict) -> str:
        action_id = action.get("id") or self._generate_id()
        self.conn.execute(
            """INSERT INTO action_plans
               (id, decision_id, decision_title, action, owner, due,
                priority, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                action_id, action["decision_id"], action.get("decision_title"),
                action["action"], action.get("owner"), action.get("due"),
                action.get("priority", "medium"), action.get("status", "pending"),
                action.get("created_at", datetime.now().isoformat()),
            ),
        )
        self.conn.commit()
        return action_id

    def get_pending_actions(self, owner: Optional[str] = None) -> List[dict]:
        if owner:
            rows = self.conn.execute(
                "SELECT * FROM action_plans WHERE status='pending' AND owner=? ORDER BY priority DESC",
                (owner,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM action_plans WHERE status='pending' ORDER BY priority DESC"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def generate_daily_brief(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        decisions = self.conn.execute(
            "SELECT * FROM decisions WHERE date(created_at)=? ORDER BY confidence DESC",
            (today,),
        ).fetchall()
        actions = self.conn.execute(
            "SELECT * FROM action_plans WHERE date(created_at)=? ORDER BY priority DESC",
            (today,),
        ).fetchall()
        return {
            "date": today,
            "decisions": [self._row_to_dict(d) for d in decisions],
            "action_items": [self._row_to_dict(a) for a in actions],
            "summary": f"{len(decisions)} decisions, {len(actions)} action items today",
        }

    # ── Helpers ────────────────────────────────────────────────

    def _row_to_dict(self, row) -> dict:
        d = dict(row)
        for field in ["alternatives_rejected", "action_items", "related_topics"]:
            if field in d and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    def _generate_id(self) -> str:
        import uuid
        return uuid.uuid4().hex[:12]

    def close(self):
        self.conn.close()
