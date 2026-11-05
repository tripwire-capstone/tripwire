# Frustration labeling tools

Two small Python scripts (standard library only, Python 3.8+).

- `turns.json`: the 49 sample turns, with no scenario or dev/test information.
- `label.py`: blind labeling in the terminal.
- `compare.py`: an agreement report for two labelers.

Run both from `eval/labels/` so the label files and reports land there.

## 1. Label (each of us, alone)

```
cd eval/labels
python ../labeling/label.py --labeler <your-name>
```

For each turn you see the earlier turns of that conversation, then the visitor message to label.
Keys: `y` yes, `n` no, `u` unsure, `b` back one turn, `q` save and quit. After y/n/u you can type a note.

Every answer is saved right away to `labels-<your-name>.json`. Quit any time and run the same command to continue.
The assistant's reply to the message you're labeling is not shown: frustration is judged from the visitor's own words.

## 2. Compare (together, after both are finished)

```
cd eval/labels
python ../labeling/compare.py labels-<name1>.json labels-<name2>.json --out agreement-report.md
```

The report shows label counts, raw agreement, Cohen's kappa (with and without "unsure"), a who-said-what table,
and every disagreement with both notes. It refuses to run until both files cover every turn
(`--allow-incomplete` overrides).

After you've talked through the disagreements, add the sample key for a breakdown by scenario and dev/test split:

```
python ../labeling/compare.py labels-<name1>.json labels-<name2>.json --key ../sample-key.csv
```
