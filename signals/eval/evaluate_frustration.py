"""Score gold turns once per question version and save them for the dashboard."""

import argparse
import json
import math
from pathlib import Path

from classifier.frustration import classify
from eval.storage import (
    ROOT,
    connect_database,
    load_gold,
    load_report,
    load_rows,
    save_probability,
    seed_gold,
)

REPORTS = ROOT / "eval" / "reports"


def evaluate(split="dev", threshold=0.5, output=None):
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    cases = load_gold(split)
    with connect_database() as connection:
        seed_gold(connection, cases)
        for row in load_rows(connection, cases):
            if row["probability"] is None:
                print(f"Classifying {row['gold_case_id']}...", flush=True)
                # Only the stored conversation item goes to the classifier.
                probability = classify(row["item"])
                save_probability(connection, row["id"], probability)
        report = load_report(connection, cases, split, threshold)

    # Keep a JSON export for scripts and sharing.
    report_path = Path(output) if output else REPORTS / f"frustration-{split}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")

    metrics = report["metrics"]
    print(f"{split}: {len(report['cases'])} cases, threshold {threshold}")
    print(
        f"Hits: {metrics['hits']} | Misses: {metrics['misses']} | "
        f"False alarms: {metrics['false_alarms']}"
    )
    print(f"Precision: {metrics['precision']:.2f} | Recall: {metrics['recall']:.2f}")
    print(f"Scores saved to Postgres. Report saved to {report_path}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate and store frustration scores.")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--output", help="Optional path for the JSON report export.")
    args = parser.parse_args()
    evaluate(split=args.split, threshold=args.threshold, output=args.output)
