#!/usr/bin/env python3
"""
Synapse — Cross-Channel Decision Linker
Finds related decisions across meetings and Slack to build the decision graph.

This runs as a periodic job (cron or n8n Schedule Trigger) to:
1. Find new decisions that haven't been linked yet
2. Compare them against existing decisions using semantic similarity
3. Create links (follows, contradicts, related, supersedes)
"""

import json
import sqlite3
from datetime import datetime
from decision_store import DecisionStore


# ── Similarity via keyword overlap (fast, no embeddings) ──────

def keyword_similarity(a: dict, b: dict) -> float:
    """Simple Jaccard similarity on related_topics + title words."""
    def words(text):
        if not text:
            return set()
        return set(text.lower().split())

    topics_a = set(json.loads(a.get("related_topics", "[]")) if isinstance(a.get("related_topics"), str) else a.get("related_topics", []))
    topics_b = set(json.loads(b.get("related_topics", "[]")) if isinstance(b.get("related_topics"), str) else b.get("related_topics", []))

    title_a = words(a.get("title", ""))
    title_b = words(b.get("title", ""))

    all_a = topics_a | title_a
    all_b = topics_b | title_b

    if not all_a or not all_b:
        return 0.0

    intersection = all_a & all_b
    union = all_a | all_b
    return len(intersection) / len(union)


def classify_relationship(a: dict, b: dict, similarity: float) -> str:
    """Determine the relationship between two decisions."""
    if similarity < 0.2:
        return None  # Not related enough

    # Check for contradiction signals
    contradict_signals = ["instead of", "rather than", "replaced by", "not using", "drop", "migrate from"]
    title_a = (a.get("title", "") + " " + a.get("decision", "")).lower()
    title_b = (b.get("title", "") + " " + b.get("decision", "")).lower()

    for signal in contradict_signals:
        if signal in title_a and any(word in title_b for word in title_a.split() if len(word) > 3):
            return "contradicts"

    # Check for superseding (newer decision about same topic)
    time_a = a.get("created_at", "")
    time_b = b.get("created_at", "")
    if similarity > 0.5 and time_a != time_b:
        return "supersedes" if time_a > time_b else "follows"

    return "related"


def link_new_decisions(store: DecisionStore, max_links_per_decision: int = 3):
    """Find and create links for unlinked decisions."""
    # Get all decisions
    all_decisions = store.get_recent_decisions(days=365, limit=500)

    if len(all_decisions) < 2:
        return {"linked": 0, "new_links": 0}

    new_links = 0

    for i, decision in enumerate(all_decisions):
        # Get existing links for this decision
        existing = store.get_related_decisions(decision["id"])
        existing_ids = {r["id"] for r in existing}

        # Find candidates
        candidates = []
        for other in all_decisions:
            if other["id"] == decision["id"] or other["id"] in existing_ids:
                continue
            sim = keyword_similarity(decision, other)
            rel = classify_relationship(decision, other, sim)
            if rel:
                candidates.append((other["id"], rel, sim))

        # Sort by similarity, take top N
        candidates.sort(key=lambda x: x[2], reverse=True)
        for other_id, relationship, sim in candidates[:max_links_per_decision]:
            store.link_decisions(decision["id"], other_id, relationship)
            new_links += 1

    return {"linked": len(all_decisions), "new_links": new_links}


# ── Via LLM (deeper semantic matching, slower) ────────────────

LLM_LINK_PROMPT = """You are a decision-linking engine. Given a NEW decision and a list of EXISTING decisions,
determine if any of the existing decisions are related to the new one.

NEW decision:
- Title: {new_title}
- Decision: {new_decision}
- Topics: {new_topics}

EXISTING decisions:
{existing_list}

For each EXISTING decision that is related, return:
- id: the existing decision's ID
- relationship: one of "follows" (builds on), "contradicts" (opposes), "supersedes" (replaces), "related" (same topic)
- reason: one sentence explaining the link

Return JSON: {{"links": [{{"id": "...", "relationship": "...", "reason": "..."}}]}}

Only return genuinely related decisions. Empty links array if nothing matches."""


if __name__ == "__main__":
    store = DecisionStore("data/decisions.db")
    result = link_new_decisions(store)
    print(f"Linked {result['linked']} decisions, created {result['new_links']} new links")

    # Show stats
    stats = store.get_stats()
    print(f"Total decisions: {stats['total_decisions']}")
    print(f"Active owners: {stats['active_owners']}")

    store.close()
