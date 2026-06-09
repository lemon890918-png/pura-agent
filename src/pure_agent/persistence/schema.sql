-- pure-agent schema v1
-- Phase 0: only create schema, no application code touches tables yet.
-- Filled in across Phases 1-9.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- ─── L0: persistence ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    hash          TEXT NOT NULL UNIQUE,
    root_path     TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_active   TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role          TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
    content_json  TEXT NOT NULL,
    token_estimate INTEGER,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id            TEXT PRIMARY KEY,
    message_id    TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    tool_name     TEXT NOT NULL,
    args_json     TEXT NOT NULL,
    result_json   TEXT,
    error         TEXT,
    latency_ms    INTEGER,
    started_at    TEXT NOT NULL,
    completed_at  TEXT
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    plan_step_id  TEXT,
    state_json    TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- ─── L6: Goal / Plan ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS goals (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    text            TEXT NOT NULL,
    constraints_json TEXT,
    status          TEXT NOT NULL CHECK (status IN ('pending','planning','running','done','failed','abandoned')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id            TEXT PRIMARY KEY,
    goal_id       TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    version       INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL CHECK (status IN ('pending','in_progress','done','failed','abandoned')),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_steps (
    id                  TEXT PRIMARY KEY,
    plan_id             TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    idx                 INTEGER NOT NULL,
    kind                TEXT NOT NULL,
    action              TEXT NOT NULL,
    deps_json           TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL CHECK (status IN ('pending','in_progress','done','failed','blocked','skipped')),
    assigned_subagent   TEXT,
    attempts            INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 3,
    last_error          TEXT,
    started_at          TEXT,
    completed_at        TEXT,
    step_report_json    TEXT,
    UNIQUE(plan_id, idx)
);

-- ─── L4: Memory 4 layers ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS memory_short (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,
    content_json  TEXT NOT NULL,
    expires_at    TEXT
);

CREATE TABLE IF NOT EXISTS memory_episodic (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    event         TEXT NOT NULL,
    importance    REAL NOT NULL DEFAULT 0.5,
    content_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_semantic (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    fact          TEXT NOT NULL,
    source        TEXT,
    confidence    REAL NOT NULL DEFAULT 1.0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_procedural (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT,
    skill_md      TEXT NOT NULL,
    examples_json TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Procedural memory: per-user preferences (separate from skills)
CREATE TABLE IF NOT EXISTS memory_user_prefs (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    content       TEXT NOT NULL,
    weight        REAL NOT NULL DEFAULT 1.0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- File tracker for diff-only re-read optimization
CREATE TABLE IF NOT EXISTS file_tracker (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    path          TEXT NOT NULL,
    mtime         REAL NOT NULL,
    size          INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,
    last_read_at  TEXT NOT NULL,
    UNIQUE (session_id, path)
);

-- Token usage for budget tracking
CREATE TABLE IF NOT EXISTS token_usage (
    id            TEXT PRIMARY KEY,
    session_id    TEXT,
    plan_id       TEXT,
    step_id       TEXT,
    model         TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_prefs_user   ON memory_user_prefs(user_id);
CREATE INDEX IF NOT EXISTS idx_file_tracker_sess ON file_tracker(session_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_plan  ON token_usage(plan_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_sess  ON token_usage(session_id);

-- ─── L5: Harness / traces ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS traces (
    id            TEXT PRIMARY KEY,
    session_id    TEXT,
    turn_id       TEXT,
    event_type    TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retries (
    id            TEXT PRIMARY KEY,
    op_id         TEXT NOT NULL,
    attempt       INTEGER NOT NULL,
    error         TEXT,
    backoff_ms    INTEGER NOT NULL,
    created_at    TEXT NOT NULL
);

-- ─── FTS5 virtual tables ────────────────────────────────────────────────────

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    message_id UNINDEXED,
    content,
    tokenize = 'porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_semantic_fts USING fts5(
    memory_id UNINDEXED,
    fact,
    tokenize = 'porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS plan_steps_fts USING fts5(
    step_id UNINDEXED,
    action,
    tokenize = 'porter unicode61'
);

-- ─── Indexes ────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_messages_session  ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_calls_msg   ON tool_calls(message_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_sess ON checkpoints(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_goals_project    ON goals(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_plans_goal       ON plans(goal_id);
CREATE INDEX IF NOT EXISTS idx_plan_steps_plan  ON plan_steps(plan_id, idx);
CREATE INDEX IF NOT EXISTS idx_memory_short     ON memory_short(session_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_memory_episodic  ON memory_episodic(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_semantic  ON memory_semantic(project_id);
CREATE INDEX IF NOT EXISTS idx_traces_session   ON traces(session_id, created_at);

-- ─── Schema version tracking ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_version (version, applied_at)
VALUES (1, datetime('now'));
