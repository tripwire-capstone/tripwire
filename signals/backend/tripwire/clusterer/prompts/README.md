# Summarizer prompts

One version = `summarize_vN.md` (the system prompt) + `summarize_vN.schema.json` (the output format).
`SUMMARIZER_PROMPT_VERSION` picks the active one; every summary row stores the version that wrote it.

**Never edit a version that has been used.** A change, even one word, is a new file with the next
number. Otherwise `event_summaries.prompt_version` would point at text that didn't write those rows.
Decisions: Design Notes Q17-Q19, `docs/clustering/DESIGN.md`.
