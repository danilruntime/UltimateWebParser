-- Drop the dead ``token`` column from ``vnc_sessions``.
--
-- It was generated for every session but never checked anywhere: noVNC is
-- served by websockify, which knows nothing about it. A secret that
-- authenticates nothing only widens the blast radius of a database leak.
--
-- SQLite refuses ``ALTER TABLE ... DROP COLUMN`` on a column with a UNIQUE
-- constraint, so the table is rebuilt. Passwords of finished sessions are
-- blanked in the same pass: those rows are history, but their plaintext
-- secret is not.

CREATE TABLE vnc_sessions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    port INTEGER NOT NULL,
    display INTEGER NOT NULL,
    password TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    started_by INTEGER,
    last_client_at TEXT,
    url TEXT,
    proxy_id INTEGER REFERENCES proxies(id) ON DELETE SET NULL
);

INSERT INTO vnc_sessions_new (
    id, profile_id, port, display, password, status, started_at, expires_at,
    started_by, last_client_at, url, proxy_id
)
SELECT
    id, profile_id, port, display,
    CASE WHEN status IN ('stopped', 'failed') THEN '' ELSE password END,
    status, started_at, expires_at, started_by, last_client_at, url, proxy_id
FROM vnc_sessions;

DROP TABLE vnc_sessions;
ALTER TABLE vnc_sessions_new RENAME TO vnc_sessions;
