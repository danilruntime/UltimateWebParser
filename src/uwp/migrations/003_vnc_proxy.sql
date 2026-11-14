-- The login browser must use the same proxy as the parse runs: logging in from
-- one IP and parsing from another is the signal the design avoids. The bridge
-- is loopback and lives in the process that launches Chromium, so the row only
-- has to remember which proxy the session was booked with.
ALTER TABLE vnc_sessions ADD COLUMN proxy_id INTEGER REFERENCES proxies(id) ON DELETE SET NULL;
