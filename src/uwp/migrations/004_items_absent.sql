-- Track whether a row was present in the previous run.
--
-- ``items`` is an append-only history: a row that disappears from the site
-- keeps its record forever, so a returning row compared equal to its stored
-- payload and was neither new nor changed. ``absent`` marks the rows the last
-- run did not see, which lets the runner treat a comeback as new.
--
-- Existing rows stay NULL, meaning "presence unknown": no earlier run recorded
-- it. Backfilling 0 would report the whole history as vanished on the first run
-- after the upgrade; backfilling 1 would report it as new. NULL makes that run
-- report neither, and every run after it is exact.
ALTER TABLE items ADD COLUMN absent INTEGER;

CREATE INDEX ix_items_absent ON items(site_id, absent);
