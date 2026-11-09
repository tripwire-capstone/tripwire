# tau-traffic

Generates realistic customer-support conversations for Tripwire to analyze, using
[τ-bench](https://github.com/sierra-research/tau2-bench) (Sierra's benchmark: a support
agent, an LLM-simulated customer, and a pass/fail grader). The agent's traces go to
Laminar, like a real app's would. Each run also writes an **answer key** (which
conversations passed or failed, and why) to check Tripwire's findings against.

Conversations can go out all at once, or **spread over time** (e.g. 40 over 8 hours),
so Tripwire's charts show traffic arriving the way it does in production.

## Requirements

- Linux or macOS
- git, and [uv](https://docs.astral.sh/uv/getting-started/installation/)
  (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- About 1.5 GB of disk (τ-bench's tasks and data)
- An OpenAI API key, and a Laminar project API key

## Setup (once)

```bash
traffic/tau-bench/setup.sh
```

It downloads τ-bench at the version we tested into `traffic/tau-bench/.tau2-bench/`
(git-ignored), installs its Python environment, and creates `traffic/tau-bench/.env`.
Put your two keys in `.env`. For self-hosted Laminar, also set the three `LMNR_` lines
there (they're explained in the file).

## Use

```bash
traffic/tau-bench/tau-traffic                 # wizard: asks everything, explains each choice
traffic/tau-bench/tau-traffic --help          # all flags
```

The wizard ends by printing the same run as a single command, so you can repeat it.
A few examples (from `traffic/tau-bench/`):

```bash
./tau-traffic --domain retail -n 3 --yes      # 3 conversations, now
./tau-traffic -n 40 --over 8h --yes           # 40 conversations, one every 12 minutes
./tau-traffic -n 40 --over 8h --dry-run       # just show the plan and cost estimate
./tau-traffic view runs/<run>                 # open a finished run in τ-bench's viewer
```

Domains: **retail** (online store), **airline**, **banking_knowledge** (the agent searches
698 policy documents, a RAG app), and **telecom** (hardest; the agent often struggles).

## What a run produces

Each run gets a folder in `traffic/tau-bench/runs/`, saved as each conversation finishes
(a crash or Ctrl+C keeps everything up to that point):

| File | What's in it |
|---|---|
| `answer_key.jsonl` | One line per conversation: Laminar session ID, task, passed or not, how it ended, cost |
| `summary.json` | Totals (pass rate, cost, how conversations ended) and the command to repeat the run |
| `transcripts.md` | Readable conversations, with every tool call and result |
| `results.json` | τ-bench's full records, including why the grader failed a conversation; open with `./tau-traffic view` |

**What Laminar receives** (default "turn" mode): one trace per customer message, grouped
into one session per conversation, with the agent's LLM calls and tool calls inside.
Every trace has `app_version` in its metadata. The simulated customer and the grader are
**never** traced: their prompts contain the expected answers.

## Costs

![What 100 conversations cost, by domain](cost-guide.svg)

<!-- cost-tables:start -->
**Per conversation** (simulated customer: gpt-4.1-mini):

| Domain | agent gpt-4o-mini | agent gpt-4.1-mini | agent gpt-4.1 | Measured |
|---|---|---|---|---|
| retail | $0.008 | $0.012 | $0.052 | $0.008 (18 conversations, gpt-4o-mini) |
| airline | $0.009 | $0.013 | $0.053 | $0.009 (10 conversations, gpt-4o-mini) |
| banking_knowledge | $0.009 | $0.013 | $0.049 | $0.009 (13 conversations, gpt-4o-mini) |
| telecom | $0.028 | $0.042 | $0.19 | $0.028 (10 conversations, gpt-4o-mini) |

**Batches** (default models; measured where available):

| Domain | 10 conversations | 50 conversations | 100 conversations | 500 conversations |
|---|---|---|---|---|
| retail | $0.081 | $0.40 | $0.81 | $4.05 |
| airline | $0.087 | $0.43 | $0.87 | $4.35 |
| banking_knowledge | $0.085 | $0.43 | $0.85 | $4.25 |
| telecom | $0.28 | $1.39 | $2.78 | $13.90 |

**Spread over time, per day** (default models):

| Domain | one every 30 min (48/day) | one every 10 min (144/day) | one every 2 min (720/day) |
|---|---|---|---|
| retail | $0.39 | $1.17 | $5.83 |
| airline | $0.42 | $1.25 | $6.26 |
| banking_knowledge | $0.41 | $1.22 | $6.12 |
| telecom | $1.33 | $4.00 | $20.02 |
<!-- cost-tables:end -->

- Most of the cost is the support agent: it re-reads its policy and tool list on every
  step and takes 10–25 steps per conversation (telecom's agent looks up dozens of records,
  so it costs about 3× more). Single conversations range from about a third to over twice
  the average.
- The default **budget is $1.00 per run**. The plan warns when the estimate is higher;
  raise it with `--budget` (or in the wizard's advanced settings).
- To update these numbers after more runs: edit `MEASURED` in `cost_guide.py` (and
  `TOKEN_PROFILES` in `tau_traffic.py` if estimates drift), then run `python3 cost_guide.py`.

## Good to know

- **Many conversations fail, even with no bugs.** τ-bench grades strictly (the final
  database must match exactly), and the default agent, gpt-4o-mini, is a small model: in
  our first 40 test conversations 7 passed (retail 3 of 10, airline 4 of 10, banking and
  telecom none). The failures are real agent mistakes, often silent (the customer leaves
  happy with a wrong result), which is exactly what Tripwire is for. Retail and airline
  give the most varied failures; banking's are mostly one kind (the agent never finds the
  special tools its documents describe), and telecom's agent often runs out of steps.
- **Stopping a run:** Ctrl+C once stops new conversations and lets running ones finish;
  twice saves immediately and quits.
- **Long runs** keep the computer awake by default (Linux: `systemd-inhibit`, macOS:
  `caffeinate`). If it sleeps anyway, the run pauses and continues on waking, without a
  burst of missed conversations.
- **Loop guards** (on by default): a simulated customer repeating itself ends the
  conversation; an agent repeating the same tool call 3 times in one turn gets a fallback
  reply ("Sorry, I'm having trouble looking that up…"), like a real app's tool-call cap.
  That reply is always the same text, so expect it to form its own cluster in Laminar.
- **Run labels** stay local unless you choose to send them to Laminar. Don't put bug names
  or expected results in a label you send.
- Already have τ-bench somewhere? Set `TAU2_BENCH_DIR` in `.env` and run `setup.sh`.

## Test checklist

- [ ] `setup.sh` on a fresh clone finishes and creates `.env`
- [ ] `./tau-traffic --domain retail -n 2 --yes`: 2 conversations; Laminar shows 2 sessions
      with one trace per customer message
- [ ] The printed `./tau-traffic view runs/<run>` opens the run
- [ ] `./tau-traffic -n 3 --over 6m --yes`: conversations start 2 minutes apart
