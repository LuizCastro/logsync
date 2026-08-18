-- Synapse Decision Store Schema
-- SQLite database for storing extracted decisions and action items

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT,
    alternatives_rejected TEXT,  -- JSON array
    owner TEXT,
    action_items TEXT,           -- JSON array
    confidence REAL DEFAULT 0.5,
    related_topics TEXT,         -- JSON array
    source TEXT NOT NULL,        -- 'meeting' or 'slack'
    meeting_id TEXT,
    channel TEXT,                -- Slack channel if source=slack
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS action_plans (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    decision_title TEXT,
    action TEXT NOT NULL,
    owner TEXT,
    due TEXT,
    priority TEXT DEFAULT 'medium',  -- 'high', 'medium', 'low'
    status TEXT DEFAULT 'pending',   -- 'pending', 'in_progress', 'done', 'cancelled'
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS decision_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id_a TEXT NOT NULL,
    decision_id_b TEXT NOT NULL,
    relationship TEXT NOT NULL,  -- 'follows', 'contradicts', 'related', 'supersedes'
    confidence REAL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    FOREIGN KEY (decision_id_a) REFERENCES decisions(id),
    FOREIGN KEY (decision_id_b) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    title TEXT,
    participants TEXT,  -- JSON array
    duration_seconds INTEGER,
    language TEXT,
    transcript TEXT,
    created_at TEXT NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_decisions_source ON decisions(source);
CREATE INDEX IF NOT EXISTS idx_decisions_owner ON decisions(owner);
CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_decisions_confidence ON decisions(confidence);
CREATE INDEX IF NOT EXISTS idx_action_plans_status ON action_plans(status);
CREATE INDEX IF NOT EXISTS idx_action_plans_owner ON action_plans(owner);
CREATE INDEX IF NOT EXISTS idx_action_plans_priority ON action_plans(priority);

-- ============================================================
-- Useful queries
-- ============================================================

-- Get all pending high-priority action items
-- SELECT * FROM action_plans WHERE status = 'pending' AND priority = 'high' ORDER BY created_at;

-- Get decisions from last 7 days
-- SELECT * FROM decisions WHERE created_at > datetime('now', '-7 days') ORDER BY created_at DESC;

-- Get decisions by owner
-- SELECT * FROM decisions WHERE owner = 'Carlos' ORDER BY created_at DESC;

-- Get all action items for a specific decision
-- SELECT * FROM action_plans WHERE decision_id = 'abc123';

-- Get decision count per day (for dashboard chart)
-- SELECT date(created_at) as day, COUNT(*) as count FROM decisions GROUP BY day ORDER BY day DESC;

-- Get decisions that contradict each other
-- SELECT d1.title, d2.title FROM decision_links dl
-- JOIN decisions d1 ON dl.decision_id_a = d1.id
-- JOIN decisions d2 ON dl.decision_id_b = d2.id
-- WHERE dl.relationship = 'contradicts';

-- Search decisions by keyword
-- SELECT * FROM decisions WHERE decision LIKE '%database%' OR title LIKE '%database%';

-- Daily brief: everything new today
-- SELECT 'decision' as type, title, owner, created_at FROM decisions
-- WHERE date(created_at) = date('now')
-- UNION ALL
-- SELECT 'action' as type, action as title, owner, created_at FROM action_plans
-- WHERE date(created_at) = date('now')
-- ORDER BY created_at DESC;
