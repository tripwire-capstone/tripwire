#!/usr/bin/env python3
"""Compare two labelers' frustration labels.

Usage:
    python compare.py labels-alice.json labels-bob.json
    python compare.py labels-alice.json labels-bob.json --out agreement-report.md
    python compare.py labels-alice.json labels-bob.json --key frustration-sample-key.csv

Run this only after BOTH of you have finished labeling: it shows each person's
labels. --key adds a breakdown by scenario and dev/test split; leave it off
until you've talked through the disagreements, so the scenario doesn't sway you.
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict

CATS = ["yes", "no", "unsure"]


def load_labels(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["labeler"], data["labels"]


def cohen_kappa(pairs, cats):
    """Agreement corrected for chance. pairs = [(label_a, label_b), ...]."""
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(a == b for a, b in pairs) / n
    count_a = Counter(a for a, _ in pairs)
    count_b = Counter(b for _, b in pairs)
    expected = sum((count_a[c] / n) * (count_b[c] / n) for c in cats)
    if expected == 1:
        return None  # both labelers used one label for everything; kappa is undefined
    return (observed - expected) / (1 - expected)


def fmt_kappa(k):
    if k is None:
        return "undefined"
    # Landis & Koch (1977) bands, the usual rough reading of kappa
    bands = [(0.0, "poor"), (0.2, "slight"), (0.4, "fair"), (0.6, "moderate"), (0.8, "substantial"), (1.01, "almost perfect")]
    word = next(w for limit, w in bands if k < limit) if k >= 0 else "worse than chance"
    return f"{k:.2f} ({word})"


def pct(x, n):
    return f"{x}/{n} ({x / n:.0%})" if n else "0/0"


def short(text, n=110):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def build_report(turns, name_a, la, name_b, lb, key):
    out = []
    w = out.append
    ids = [t["turn_id"] for t in turns]
    by_id = {t["turn_id"]: t for t in turns}
    both = [i for i in ids if i in la and i in lb]
    pairs = [(la[i]["label"], lb[i]["label"]) for i in both]

    w(f"# Frustration labeling: {name_a} vs {name_b}\n")
    w(f"Turns in sample: {len(ids)} · labeled by both: {len(both)}\n")

    # --- Label counts
    w("## Label counts\n")
    w(f"| Label | {name_a} | {name_b} |")
    w("| --- | --- | --- |")
    for c in CATS:
        ca = sum(la[i]["label"] == c for i in both)
        cb = sum(lb[i]["label"] == c for i in both)
        w(f"| {c} | {ca} | {cb} |")
    w("")

    # --- Agreement
    agree = sum(a == b for a, b in pairs)
    w("## Agreement\n")
    w(f"- **All three labels (yes/no/unsure):** {pct(agree, len(pairs))} agree; Cohen's kappa {fmt_kappa(cohen_kappa(pairs, CATS))}")
    sure = [(a, b) for a, b in pairs if a != "unsure" and b != "unsure"]
    agree_sure = sum(a == b for a, b in sure)
    w(f"- **Yes/no only** (turns where neither said unsure, {len(sure)} turns): "
      f"{pct(agree_sure, len(sure))} agree; Cohen's kappa {fmt_kappa(cohen_kappa(sure, ['yes', 'no']))}")
    w(f"- Turns where at least one of you said unsure: {len(pairs) - len(sure)}")
    w("")
    w("Kappa corrects for agreement you'd get by chance. When most turns are \"no\", raw agreement looks high even if you rarely agree on the \"yes\" turns, which is why both numbers are shown.\n")

    # --- Confusion matrix
    w("## Who said what\n")
    w(f"Rows: {name_a}. Columns: {name_b}.\n")
    w("| | " + " | ".join(CATS) + " |")
    w("| --- |" + " --- |" * len(CATS))
    grid = Counter(pairs)
    for a in CATS:
        w(f"| **{a}** | " + " | ".join(str(grid[(a, b)]) for b in CATS) + " |")
    w("")

    # --- Disagreements
    dis = [i for i in both if la[i]["label"] != lb[i]["label"]]
    w(f"## Disagreements to talk through ({len(dis)})\n")
    if not dis:
        w("None.\n")
    for i in dis:
        t = by_id[i]
        w(f"**{i}** (conversation {t['conversation']}, turn {t['turn']}): {name_a} **{la[i]['label']}**, {name_b} **{lb[i]['label']}**")
        w(f"> {short(t['user'], 300)}")
        for name, l in ((name_a, la), (name_b, lb)):
            if l[i].get("note"):
                w(f"- {name}'s note: {l[i]['note']}")
        w("")

    # --- Optional breakdown by the sample key
    if key:
        w("## Breakdown by sample key\n")
        for field in ("split", "target_bug"):
            groups = defaultdict(list)
            for i in both:
                if i in key:
                    groups[key[i][field]].append(i)
            w(f"| {field} | turns | agree | {name_a} yes | {name_b} yes |")
            w("| --- | --- | --- | --- | --- |")
            for g in sorted(groups):
                gi = groups[g]
                ag = sum(la[i]["label"] == lb[i]["label"] for i in gi)
                ya = sum(la[i]["label"] == "yes" for i in gi)
                yb = sum(lb[i]["label"] == "yes" for i in gi)
                w(f"| {g} | {len(gi)} | {pct(ag, len(gi))} | {ya} | {yb} |")
            w("")

    return "\n".join(out)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Compare two labelers' frustration labels.")
    ap.add_argument("labels_a")
    ap.add_argument("labels_b")
    ap.add_argument("--turns", default=os.path.join(here, "turns.json"))
    ap.add_argument("--key", help="sample key CSV; adds a breakdown by split and scenario")
    ap.add_argument("--out", help="also write the report to this Markdown file")
    ap.add_argument("--allow-incomplete", action="store_true", help="compare even if someone hasn't finished")
    args = ap.parse_args()

    with open(args.turns, encoding="utf-8") as f:
        turns = json.load(f)["turns"]
    name_a, la = load_labels(args.labels_a)
    name_b, lb = load_labels(args.labels_b)
    if name_a == name_b:
        sys.exit(f"Both files belong to '{name_a}'. Pass two different labelers' files.")

    ids = {t["turn_id"] for t in turns}
    missing = {n: sorted(ids - set(l)) for n, l in ((name_a, la), (name_b, lb))}
    unfinished = {n: m for n, m in missing.items() if m}
    if unfinished and not args.allow_incomplete:
        for n, m in unfinished.items():
            print(f"{n} hasn't labeled {len(m)} turn(s): {', '.join(m[:10])}{' …' if len(m) > 10 else ''}")
        sys.exit("Finish labeling first, or pass --allow-incomplete.")

    key = None
    if args.key:
        with open(args.key, encoding="utf-8") as f:
            key = {r["turn_id"]: r for r in csv.DictReader(f)}

    report = build_report(turns, name_a, la, name_b, lb, key)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\nReport written to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
