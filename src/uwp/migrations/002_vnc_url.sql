-- VNC sessions are requested by the bot and started by the worker, so the
-- session row must remember which page the login browser should open.
ALTER TABLE vnc_sessions ADD COLUMN url TEXT;
