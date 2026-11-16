# compare-laminar

Compares Tripwire's signals against Laminar Cloud's own Signals on the same traffic, so
Tripwire's quality can be tuned against a real reference. Feeds the case study.

One run = one traffic source (park-traffic **or** tau-bench, never both — they need different
ground truth, see "Reading a report" below) judged by both systems, snapshotted, and compared.

## What's manual vs. automated

Manual, once per fresh run (the wizard walks you through these):
- Create a fresh Laminar Cloud project (clean run: no old traces leaking in).
- Create each signal in `signals/` in Laminar's dashboard (or `lmnr-cli signal create`),
  copying the prompt/schema from the file so both systems judge with the same definition. No
  automated path for this side — Laminar has no API we call from here, only its dashboard/CLI.
- Point Tripwire at the new project (new `LMNR_PROJECT_API_KEY`) and reset its database.
- Create the matching signal in Tripwire's own UI — **or** let the wizard do it: it can parse
  each `signals/*.md` file's prompt/schema directly and `POST` it to Tripwire's own
  `/api/signals`, showing what it would create and asking once before doing anything. Opt-in,
  same as the `.env` automation below — the file stays the single source of truth either way.

Automated (`./compare-laminar`, once setup is done):
- Runs the chosen traffic generator (park-traffic or tau-bench).
- Waits for both systems to settle.
- Snapshots both, plus whatever ground truth that traffic type has (park-traffic's planted-bug
  answer key; tau-bench's pass/fail results and a direct read of Laminar's raw spans for the
  raw-JSON-reply check).
- Writes `runs/<label>/` with the raw data (JSON) and a readable `report.md`.

## Signals compared

See `signals/`. Each file is the prompt + output schema as text, for you to copy into both
systems — not executed by any script. The wizard's manual-step prompts glob `signals/*.md`
directly (non-recursive), so every file there is treated as part of the current run — park a
signal you're not using this time in `signals/parked/` so the wizard doesn't prompt for it (see
`signals/parked/task_evaluator.md`: not part of the three signals actually running as of
2026-11-16 — Issues, Intent, User frustration/Frustration, all matched by hand, see each file).

## Setup

```bash
cd eval/compare-laminar
cp .env.example .env   # fill in after the wizard's manual steps
```

Requires `TEST_DATABASE_URL` (Tripwire's Postgres — same var the backend tests use) and a
Laminar project API key, same as `signals/backend/.env.example`.

## Tests

```bash
.venv/bin/python -m pytest tests/
```

Covers the pure logic (agreement bucketing, cluster-chain walking, ground-truth dimensions,
the two LLM-output shapes) — no live DB or Laminar project needed. Doesn't cover
`snapshot_tripwire()`/`snapshot_laminar()` themselves; those were verified by actually running
them against a real project (see Status below), which a unit test can't substitute for.

## Running

```bash
./compare-laminar --traffic park --n 50 --label glacier-fix-attempt-1
./compare-laminar --traffic tau --n 20 --label retail-smoke --traffic-args "--domain retail"
```

`--traffic-args` passes anything through verbatim to the underlying generator — e.g. tau-bench's
`--domain`/`--mode`/`--agent-model`, or park-traffic's `--mix`/`--bugs`. Not re-exposed as their
own flags here on purpose: that surface would drift out of sync as those tools grow. Check each
tool's own `--help` for what's available; an override wins over this tool's defaults since it's
appended last and both tools' argparse takes the last occurrence of a repeated flag.

See `--help` for all flags. `view runs/<label>/report.md` for the result.

## Reading a report

Each signal pair gets one "vs. &lt;dimension&gt;" table per ground-truth dimension that traffic
type has — more than one when they're independent facts about a trace (a tau-bench conversation
can pass *and* reply in raw JSON, or fail and never once reply in raw JSON; combining them into
one table would obscure that).

- **park-traffic runs:** one dimension — the planted-bug answer key. The report crosstabs
  flagged/clustered events against the planted cause (purity), same framing as the Nov 12 live
  test that found `large_party` + the sold-out decoy + no-bug visitors merged into one cluster.
- **tau-bench runs:** two dimensions — the benchmark's own pass/fail per conversation
  (sensitivity/false-positive rate against that), and a known quirk check: the agent replies
  with raw JSON ~13% of the time (`current-state.md`) — does clustering actually separate that
  out, or merge it into something generic? The quirk check reads straight from Laminar's own
  spans, not Tripwire's judge_input, so it isn't affected by the judge-rendering bug filed
  2026-11-16.
- **Every run:** a noise baseline — Tripwire judges the same traces twice, so a disagreement
  with Laminar under that self-disagreement rate is noise, not signal. Not built yet.

## Status

As of 2026-11-16: both traffic paths are proven against real fresh projects end to end — real
traffic, real judging on both sides, real reports. park-traffic at smoke scale (20
conversations); tau-bench at a real batch size (100 conversations, 452 traces). Along the way
this caught and fixed real bugs in the tool itself (a uuid.UUID-vs-string mismatch in the
cross-system join; a markdown-parsing bug that would have inserted spurious mid-sentence line
breaks into signal instructions; a stale-Docker-image/stale-API-key pair of infra issues that
are now operational lessons, not code problems) — each caught by actually running it, not by
inspection.

**Two things to know before trusting a report's numbers, neither of which this tool can detect
for you:**

1. **A judge-rendering bug in Tripwire's own `tripwire/judge/render.py`** (filed 2026-11-16)
   silently drops the assistant's reply/tool-call content for any trace shaped like a raw
   OpenAI SDK response rather than LiteLLM's — confirmed to affect **park-traffic** (direct
   OpenAI calls), confirmed *not* to affect **tau-bench** (goes through LiteLLM). Until that's
   fixed, treat park-traffic's Issues-signal numbers as unreliable; tau-bench's are clean of
   this specific problem.
2. **A misconfigured signal on Laminar's side can produce a confusing-looking report with no
   error or warning.** Caught live on the 100-conversation tau-bench run: Intent's table showed
   `laminar_only=0, neither=0` — a clean split that looks like strong agreement, but actually
   means Laminar silently didn't run Intent at all on 127 of 452 traces (ruled out a token
   filter by checking directly; the pattern is consistent with a sub-100% sampling rate on that
   signal specifically, not present on Issues/User frustration). **Before trusting a run,
   check each signal's filters/sampling in Laminar's dashboard** — this tool reports exactly
   what it sees and has no way to know Laminar's settings don't match what you intended.

Also still missing, called out explicitly in every report rather than faked:
- **Noise baseline** — needs a second Tripwire judging pass over the same traces; that feature
  doesn't exist yet. Without it, there's no way to tell how much of any disagreement number
  reflects real judge difference versus ordinary judge-to-judge noise.

Cluster comparison already handles more than one level each side (`LEVEL_OFFSET` in
`compare.py`), ready for when Tripwire's clusters grow a tree (up to 3 levels planned — today
only level 0 exists).

`SIGNAL_PAIRS` in `compare.py` has three real entries for the project set up 2026-11-16 (Issues,
Intent, User frustration/Frustration — all matched by hand, see `signals/*.md`). Edit it to
match whichever project you're running against.
