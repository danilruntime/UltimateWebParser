-- Initial schema for UltimateWebParser.
-- Timestamps are stored as UTC text in the ``YYYY-MM-DDTHH:MM:SSZ`` format
-- (see ``uwp.timeutil``); JSON columns hold ``json.dumps`` output; enums hold
-- their string values.

CREATE TABLE sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    parser_key TEXT NOT NULL,
    parser_settings TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 0,
    profile_mode TEXT NOT NULL DEFAULT 'clean',
    profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    proxy_id INTEGER REFERENCES proxies(id) ON DELETE SET NULL,
    interval_min_min INTEGER NOT NULL,
    interval_max_min INTEGER NOT NULL,
    next_run_at TEXT,
    export_mode TEXT NOT NULL DEFAULT 'always',
    max_attempts INTEGER NOT NULL DEFAULT 2,
    last_run_at TEXT,
    last_status TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    match_profile_to_proxy_country INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    seed INTEGER NOT NULL,
    platform TEXT NOT NULL,
    brand TEXT NOT NULL,
    brand_version TEXT NOT NULL,
    timezone TEXT NOT NULL,
    locale TEXT NOT NULL,
    user_data_dir TEXT NOT NULL,
    proxy_id INTEGER REFERENCES proxies(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'free',
    lease_until TEXT,
    last_used_at TEXT,
    expires_at TEXT
);

CREATE TABLE proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    scheme TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    username TEXT,
    password TEXT,
    country TEXT
);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT,
    claimed_at TEXT,
    heartbeat_at TEXT,
    not_before TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX ux_jobs_active ON jobs(site_id) WHERE status IN ('queued', 'running');

CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    rows_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    report_path TEXT,
    artifact_dir TEXT,
    error TEXT,
    duration_ms INTEGER,
    profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    proxy_id INTEGER REFERENCES proxies(id) ON DELETE SET NULL
);

CREATE INDEX ix_runs_site ON runs(site_id, started_at);

CREATE TABLE items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    external_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE (site_id, external_key)
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE event_deliveries (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    admin_id INTEGER NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (event_id, admin_id)
);

CREATE TABLE admins (
    telegram_id INTEGER PRIMARY KEY,
    notify INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL
);

CREATE TABLE vnc_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    port INTEGER NOT NULL,
    display INTEGER NOT NULL,
    password TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    started_by INTEGER,
    last_client_at TEXT
);

CREATE TABLE worker_heartbeat (
    worker_id TEXT PRIMARY KEY,
    heartbeat_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
