# park-traffic

The Park Trip Assistant: a small chatbot with planted bugs, plus simulated visitors. For most traffic, use tau-traffic ([cheat sheet](CHEATSHEET.md)); park-traffic is for runs where you want specific planted bugs and an answer key of which bug fired.

Repo: [vkarklins/park-traffic](https://github.com/vkarklins/park-traffic). Laminar settings are in its `.env` (`LMNR_BASE_URL`, ports, key); use the same Laminar project as `signals/backend/.env`.

First time:

```bash
git clone https://github.com/vkarklins/park-traffic.git ~/projects/park-traffic
cd ~/projects/park-traffic
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your OpenAI and Laminar keys
```

Each time:

```bash
cd ~/projects/park-traffic
source .venv/bin/activate
python -m harness.run --n 8 --mix baseline --bugs all --seed 2 --label demo   # ~1.5 cents
```

| To do this | Run |
| --- | --- |
| Try it without sending anything to Laminar | `python -m harness.run --n 3 --dry-run --label dry` |
| Clean traffic, no bugs, capped at 60 events | `python -m harness.run --n 10 --mix clean --label smoke --max-events 60` |
| Only some bugs | `--bugs large_party,date_shift` |
| Re-render an old run's transcripts | `python -m harness.render runs/<run-folder>` |

- Bugs: `large_party`, `date_shift` (visible only in traces), `forgetting`, `glacier_mixup`, `timeout`. Defaults: `--mix clean`, no bugs.
- One turn = one trace; one conversation = one Laminar session. `--max-events` is a hard cap.
- Each run folder in `runs/` has `answer_key.jsonl` (target bug, bugs that fired), `transcripts.md`, `summary.json`.
