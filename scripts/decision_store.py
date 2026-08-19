#!/usr/bin/env python3
"""
Synapse — Decision Store (SQLite)
Handles persistence for extracted decisions and action plans.

Usage:
    from decision_store import DecisionStore

    store = DecisionStore("data/decisions.db")
    store.insert_decision({...})
    store.get_recent_decisions(days=7)
"""

import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


class DecisionStore:
    def __init__(self, db_path: str = "data/decisions.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        schema_path = Path(__file__).parent / "schema.sql"
        with open(schema_path) as f:
            self.conn.executescript(f.read())

    # ── Decisions ──────────────────────────────────────────────

    def insert_decision(self, decision: dict) -> str:
        decision_id = decision.get("id") or self._generate_id()
        self.conn.execute(
            """INSERT OR REPLACE INTO decisions
               (id, title, decision, rationale, alternatives_rejected,
                owner, action_items, confidence, related_topics,
                source, meeting_id, channel, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id,
                decision["title"],
                decision["decision"],
                decision.get("rationale"),
                json.dumps(decision.get("alternatives_rejected", [])),
                decision.get("owner"),
                json.dumps(decision.get("action_items", [])),
                decision.get("confidence", 0.5),
                json.dumps(decision.get("related_topics", [])),
                decision.get("source", "meeting"),
                decision.get("meeting_id"),
                decision.get("channel"),
                decision.get("created_at", datetime.now().isoformat()),
            ),
        )
        self.conn.commit()
        return decision_id

    def get_decision(self, decision_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_recent_decisions(self, days: int = 7, limit: int = 50) -> list:
        rows = self.conn.execute(
            """SELECT * FROM decisions
               WHERE datetime(created_at) > datetime('now', ?)
               ORDER BY created_at DESC LIMIT ?""",
            (f"-{days} days", limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def search_decisions(self, keyword: str, limit: int = 20) -> list:
        rows = self.conn.execute(
            """SELECT * FROM decisions
               WHERE decision LIKE ? OR title LIKE ? OR rationale LIKE ?
               ORDER BY confidence DESC LIMIT ?""",
            (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── Action Plans ───────────────────────────────────────────

    def insert_action_item(self, action: dict) -> str:
        action_id = action.get("id") or self._generate_id()
        self.conn.execute(
            """INSERT INTO action_plans
               (id, decision_id, decision_title, action, owner, due,
                priority, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                action_id,
                action["decision_id"],
                action.get("decision_title"),
                action["action"],
                action.get("owner"),
                action.get("due"),
                action.get("priority", "medium"),
                action.get("status", "pending"),
                action.get("created_at", datetime.now().isoformat()),
            ),
        )
        self.conn.commit()
        return action_id

    def get_pending_actions(self, owner: Optional[str] = None) -> list:
        if owner:
            rows = self.conn.execute(
                """SELECT * FROM action_plans
                   WHERE status = 'pending' AND owner = ?
                   ORDER BY priority DESC, created_at""",
                (owner,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM action_plans
                   WHERE status = 'pending'
                   ORDER BY priority DESC, created_at"""
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_actions(self, limit: int = 200) -> list:
        rows = self.conn.execute(
            """SELECT * FROM action_plans
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def complete_action(self, action_id: str):
        self.conn.execute(
            """UPDATE action_plans
               SET status = 'done', completed_at = ?
               WHERE id = ?""",
            (datetime.now().isoformat(), action_id),
        )
        self.conn.commit()

    # ── Links ──────────────────────────────────────────────────

    def link_decisions(self, id_a: str, id_b: str, relationship: str):
        self.conn.execute(
            """INSERT INTO decision_links
               (decision_id_a, decision_id_b, relationship, created_at)
               VALUES (?, ?, ?, ?)""",
            (id_a, id_b, relationship, datetime.now().isoformat()),
        )
        self.conn.commit()

    def get_related_decisions(self, decision_id: str) -> list:
        rows = self.conn.execute(
            """SELECT d.*, dl.relationship
               FROM decision_links dl
               JOIN decisions d ON (
                   (dl.decision_id_a = ? AND d.id = dl.decision_id_b)
                   OR (dl.decision_id_b = ? AND d.id = dl.decision_id_a)
               )
               WHERE dl.decision_id_a = ? OR dl.decision_id_b = ?""",
            (decision_id, decision_id, decision_id, decision_id),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── Dashboard Queries ──────────────────────────────────────

    def get_daily_stats(self, days: int = 30) -> list:
        rows = self.conn.execute(
            """SELECT date(created_at) as day,
                      COUNT(*) as decisions_count,
                      AVG(confidence) as avg_confidence
               FROM decisions
               WHERE datetime(created_at) > datetime('now', ?)
               GROUP BY day ORDER BY day""",
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        decisions = self.conn.execute("SELECT COUNT(*) as c FROM decisions").fetchone()["c"]
        actions_pending = self.conn.execute(
            "SELECT COUNT(*) as c FROM action_plans WHERE status='pending'"
        ).fetchone()["c"]
        actions_done = self.conn.execute(
            "SELECT COUNT(*) as c FROM action_plans WHERE status='done'"
        ).fetchone()["c"]
        owners = self.conn.execute(
            "SELECT DISTINCT owner FROM decisions WHERE owner IS NOT NULL"
        ).fetchall()
        return {
            "total_decisions": decisions,
            "pending_actions": actions_pending,
            "completed_actions": actions_done,
            "active_owners": [r["owner"] for r in owners],
        }

    def generate_daily_brief(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        decisions = self.conn.execute(
            """SELECT title, decision, owner, rationale, confidence
               FROM decisions WHERE date(created_at) = ?
               ORDER BY confidence DESC""",
            (today,),
        ).fetchall()
        actions = self.conn.execute(
            """SELECT action, owner, priority, decision_title
               FROM action_plans WHERE date(created_at) = ?
               ORDER BY priority DESC""",
            (today,),
        ).fetchall()
        return {
            "date": today,
            "decisions": [dict(r) for r in decisions],
            "action_items": [dict(r) for r in actions],
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


if __name__ == "__main__":
    store = DecisionStore("data/decisions.db")

    # Demo: insert a test decision
    test_decision = {
        "title": "Use PostgreSQL for user data",
        "decision": "We will use PostgreSQL as the primary database for user data.",
        "rationale": "Team experience + relational integrity needed for user model.",
        "alternatives_rejected": ["MongoDB — schema flexibility not needed", "Firebase — vendor lock-in"],
        "owner": "Carlos",
        "action_items": [
            "Carlos to set up PostgreSQL schema by Friday",
            "Maria to migrate existing queries",
        ],
        "confidence": 0.92,
        "related_topics": ["database", "architecture"],
        "source": "meeting",
        "meeting_id": "demo_001",
    }

    decision_id = store.insert_decision(test_decision)
    print(f"Inserted decision: {decision_id}")

    # Insert action items
    for item in test_decision["action_items"]:
        store.insert_action_item({
            "decision_id": decision_id,
            "decision_title": test_decision["title"],
            "action": item,
            "owner": test_decision["owner"],
            "priority": "high",
        })

    # Get stats
    stats = store.get_stats()
    print(f"Stats: {json.dumps(stats, indent=2)}")

    # Get daily brief
    brief = store.generate_daily_brief()
    print(f"Daily brief: {json.dumps(brief, indent=2)}")

    store.close()
