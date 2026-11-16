# Intent

Same name on both sides. Instructions/schema matched by hand (2026-11-16).

**Instruction:**

> What was the user trying to accomplish in this session?
> Classify the primary intent.

**Output schema:**

| field | type | nullable | description |
|---|---|---|---|
| `Intent` | string | no | The user's primary intent |
| `Complexity` | enum (`simple`/`moderate`/`complex`) | no | Complexity of the user's request |

**Trigger / filters:** trace finishes (default); no filter on either side as of 2026-11-16.
