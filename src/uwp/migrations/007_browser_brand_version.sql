-- Bring the stored browser version in line with the pinned binary.
--
-- The browser is pinned to fingerprint-chromium 148 (see ``uwp.config``), and a
-- profile row carries the Chrome major it told the site it was. A row written
-- under the old 150 pin makes the User-Agent say 148 while ``sec-ch-ua`` and
-- ``navigator.userAgentData`` say 150 — a mix no real browser emits, and one
-- that a site sees in plain headers. Only the major is normalised: the
-- fingerprint seed, platform, brand, locale and timezone are the profile's
-- identity and must not change.
UPDATE profiles SET brand_version = '148' WHERE brand_version = '150';
