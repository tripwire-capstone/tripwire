-- Keys saved on the Settings page (SIG-103), encrypted by the app (tripwire/crypto.py) with
-- TRIPWIRE_SECRET_KEY. NULL = nothing saved: the workers fall back to the key in their environment
-- (LMNR_PROJECT_API_KEY / OPENAI_API_KEY from .env locally, SSM on AWS).
--
-- Hints are the first 4 and last 4 characters ("TGmC...vvzt"), as Laminar shows its keys.

ALTER TABLE projects
    ALTER COLUMN laminar_api_key_encrypted DROP NOT NULL,
    ALTER COLUMN laminar_api_key_hint DROP NOT NULL,
    ADD COLUMN openai_api_key_encrypted bytea,
    ADD COLUMN openai_api_key_hint text;

-- The seed used to store a placeholder instead of a real key; it was never a saved key.
UPDATE projects
SET laminar_api_key_encrypted = NULL, laminar_api_key_hint = NULL
WHERE laminar_api_key_encrypted = 'not-encrypted-yet'::bytea;

-- A key and its hint are saved and cleared together.
ALTER TABLE projects
    ADD CONSTRAINT projects_laminar_key_with_hint
        CHECK ((laminar_api_key_encrypted IS NULL) = (laminar_api_key_hint IS NULL)),
    ADD CONSTRAINT projects_openai_key_with_hint
        CHECK ((openai_api_key_encrypted IS NULL) = (openai_api_key_hint IS NULL));
