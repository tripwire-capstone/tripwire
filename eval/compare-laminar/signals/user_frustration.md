# User frustration / User Frustration

Named "User frustration" in Tripwire, "User Frustration" in Laminar (capitalization only —
cosmetic, doesn't affect `SIGNAL_PAIRS` matching since each side is just a literal string).
Instructions/schema matched by hand across both systems (2026-11-16); version 3 in Tripwire
after fixing a paste-into-the-middle corruption in version 2 (the new text landed mid-string
in the old instruction instead of replacing it — caught by reading the DB directly, not the UI).

**Instruction:**

> Analyze this session for signs of user frustration or friction.
> Look for confusion, repeated attempts, or poor user experience.

**Output schema:**

| field | type | nullable | description |
|---|---|---|---|
| `severity` | enum (`low`/`medium`/`high`) | no | Severity of the user friction |
| `description` | string | no | Description of the friction or frustration observed |

**Trigger / filters:** trace finishes (default); no filter on either side as of 2026-11-16 (the
token-count filter that ships on Laminar's presets by default was removed on the Laminar side
to match Tripwire's `filters: []`).
