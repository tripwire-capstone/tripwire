# Task Evaluator (Laminar preset, 4th/secondary)

**Correction (2026-11-15):** earlier guess here was wrong. Checked the live trial project
(`signals/backend/.env`) directly via SQL — it has an active signal named "Task Evaluator v2",
with 8689+ events on record Oct 28–Nov 13. Task Evaluator isn't gone or deprecated; it's alive
and has clearly been edited since creation (the "v2"). The Nov 2026 changelog's rename
(Failure Detector/Intent Classifier/Friction Detector → Issues/Intent/Friction) only touches the
**onboarding template list** for brand-new signals — it doesn't affect signals already created
under the old names, and doesn't by itself prove Task Evaluator can't still be created fresh
either. Still included anyway for comparison, weighted less heavily than the three primary
presets in the report, per your call.

**Still open:** get the exact prompt/schema for the existing "Task Evaluator v2" via
`lmnr-cli signal get` (or the dashboard) so this file can hold the real wording, the same way
`user_frustration.md` holds Tripwire's real instruction. SQL only returns signal output, not the
signal's own definition.

**What it does (from the Oct 27 field notes, now confirmed live):** compares tool input/output +
reply + system prompt against what should have happened; explains with evidence. Caught the
`date_shift` planted bug (trace-only) that Tripwire's own date-shift eval only caught
secondarily (via the bot's reply, not the root-cause tool bug).
