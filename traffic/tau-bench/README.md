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
698 policy documents, a RAG app), and **telecom** (phone troubleshooting).

Models: by default **gpt-6-luna** plays both the support agent and the simulated customer
(newest and cheapest). Pick **gpt-4o-mini** as the agent for a different mix of mistakes:
louder ones (transfers to a human, frustrated customers) instead of mostly silent ones.

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
**Per conversation:**

| Domain | gpt-6-luna agent + gpt-6-luna customer (default) | gpt-4o-mini agent + gpt-4.1-mini customer |
|---|---|---|
| retail | $0.0017 (20 conversations) | $0.0094 (28 conversations) |
| airline | $0.0018 (10 conversations) | $0.0087 (10 conversations) |
| banking_knowledge | $0.014 (6 conversations) | $0.0085 (13 conversations) |
| telecom | $0.0023 (10 conversations) | $0.028 (10 conversations) |

Other agent models (with the gpt-6-luna customer; rough estimates, not measured): gpt-4.1-mini retail $0.0086, airline $0.0096, banking_knowledge $0.098, telecom $0.013; gpt-4.1 retail $0.041, airline $0.046, banking_knowledge $0.49, telecom $0.062.

**Batches** (default models):

| Domain | 10 conversations | 50 conversations | 100 conversations | 500 conversations |
|---|---|---|---|---|
| retail | $0.017 | $0.084 | $0.17 | $0.84 |
| airline | $0.018 | $0.091 | $0.18 | $0.91 |
| banking_knowledge | $0.14 | $0.70 | $1.40 | $6.98 |
| telecom | $0.023 | $0.11 | $0.23 | $1.13 |

**Spread over time, per day** (default models):

| Domain | one every 30 min (48/day) | one every 10 min (144/day) | one every 2 min (720/day) |
|---|---|---|---|
| retail | $0.081 | $0.24 | $1.21 |
| airline | $0.087 | $0.26 | $1.31 |
| banking_knowledge | $0.67 | $2.01 | $10.05 |
| telecom | $0.11 | $0.33 | $1.63 |
<!-- cost-tables:end -->

- Most of the cost is the support agent: it re-reads its policy and tool list on every
  step and takes 10–25 steps per conversation. With gpt-6-luna, banking costs the most:
  the agent searches the policy documents many times per conversation. Single
  conversations range from about a third to over twice the average.
- The default **budget is $1.00 per run**. The plan warns when the estimate is higher;
  raise it with `--budget` (or in the wizard's advanced settings).
- To update these numbers after more runs: edit `MEASURED` in `cost_guide.py` (and
  `TOKEN_PROFILES` in `tau_traffic.py` if estimates drift), then run `python3 cost_guide.py`.

## Good to know

- **Many conversations fail, even with no bugs.** τ-bench grades strictly (the final
  database must match exactly). In our test runs:

  | Domain | gpt-6-luna (default) passed | gpt-4o-mini agent passed |
  |---|---|---|
  | retail | 11 of 20 | 8 of 20 |
  | airline | 2 of 10 | 4 of 10 |
  | banking_knowledge | 0 of 6 | 0 of 10 |
  | telecom | 4 of 10 | 0 of 10 (most ran out of steps) |

  The failures are real agent mistakes. gpt-6-luna's are mostly **silent**: the customer
  leaves happy with a wrong result (wrong item exchanged, part of a request skipped),
  plus transfers to a human in telecom. That's exactly what Tripwire is for, and a
  frustration signal alone won't catch it. gpt-4o-mini's are louder and more varied.
- **gpt-6-luna sometimes replies in raw JSON** (about 1 reply in 8), e.g.
  `{"message":"I can help check the exchange options…"}`. It's the agent's own quirk,
  left as is: a real app with this setup would show it to customers. Expect it to form
  its own cluster in Laminar.
- **The simulated customer is retried** if it returns an empty message (gpt-6-luna
  sometimes does, in banking). That's test equipment, not the app, so it's invisible in
  Laminar; the answer key counts it as `customer_retries`.
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
