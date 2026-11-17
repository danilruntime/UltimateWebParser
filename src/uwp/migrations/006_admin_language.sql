-- The language an admin chose in the bot. NULL means "no opinion": the bot
-- falls back to the Telegram client's language_code, then to
-- UWP_DEFAULT_LANGUAGE. A column rather than a JSON blob because the gate
-- reads it on every single update.
ALTER TABLE admins ADD COLUMN language TEXT;
