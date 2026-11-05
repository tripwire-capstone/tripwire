#!/usr/bin/env python3
"""Blind frustration labeling in the terminal.

Usage:
    python label.py --labeler alice
    python label.py --labeler alice --turns turns.json --out labels-alice.json

Shows each conversation one turn at a time: earlier turns as context, then the
user message to label. Your answers are saved after every keypress, so you can
quit (q) and pick up where you left off by running the same command again.

Keys:  y = frustrated   n = not frustrated   u = unsure
       b = back one turn   q = save and quit
After y/n/u you can type an optional note (Enter to skip).
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import textwrap
from datetime import datetime, timezone

LABELS = {"y": "yes", "n": "no", "u": "unsure"}

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def style(text, code):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def wrap(text, indent="    "):
    width = max(40, min(100, shutil.get_terminal_size((100, 24)).columns) - len(indent))
    paras = text.splitlines() or [""]
    return "\n".join(
        textwrap.fill(p, width, initial_indent=indent, subsequent_indent=indent) if p.strip() else ""
        for p in paras
    )


def load_turns(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["turns"]


def load_labels(path, labeler, turns_path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("labeler") != labeler:
            sys.exit(f"{path} belongs to labeler '{data.get('labeler')}', not '{labeler}'. Use --out to pick another file.")
        return data
    return {
        "labeler": labeler,
        "turns_file": os.path.basename(turns_path),
        "started_at": now(),
        "labels": {},
    }


def save_labels(path, data):
    """Write to a temp file, then rename, so a crash never leaves a half-written file."""
    data["updated_at"] = now()
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".labels-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def show_turn(turns, i, done, total):
    t = turns[i]
    convo = [x for x in turns if x["conversation"] == t["conversation"]]
    n_in_convo = len(convo)
    os.system("cls" if os.name == "nt" else "clear") if sys.stdout.isatty() else print("\n" + "=" * 60)

    print(style(f"Turn {t['turn_id']}  ·  conversation {t['conversation']}, turn {t['turn']} of {n_in_convo}"
                f"  ·  {done}/{total} labeled", "2"))
    earlier = [x for x in convo if x["turn"] < t["turn"]]
    if earlier:
        print(style("\nEarlier in this conversation", "1"))
        for x in earlier:
            print(style(f"  Visitor (turn {x['turn']}):", "36"))
            print(wrap(x["user"]))
            print(style(f"  Assistant:", "2"))
            print(style(wrap(x["reply"]), "2"))
    print(style("\nLabel this visitor message", "1;33"))
    print(wrap(t["user"]))


def prompt(existing):
    hint = f"  (current: {existing['label']})" if existing else ""
    while True:
        key = input(f"\nFrustrated?  [y]es  [n]o  [u]nsure   [b]ack  [q]uit{hint}: ").strip().lower()
        if key in LABELS or key in ("b", "q"):
            return key
        if key == "" and existing:
            return "keep"
        print("Please type y, n, u, b or q.")


def main():
    ap = argparse.ArgumentParser(description="Blind frustration labeling.")
    ap.add_argument("--labeler", required=True, help="your name, e.g. alice")
    ap.add_argument("--turns", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "turns.json"))
    ap.add_argument("--out", help="output file (default: labels-<labeler>.json)")
    args = ap.parse_args()

    out = args.out or f"labels-{args.labeler}.json"
    turns = load_turns(args.turns)
    data = load_labels(out, args.labeler, args.turns)
    labels = data["labels"]

    # Start at the first unlabeled turn (resume support).
    i = next((k for k, t in enumerate(turns) if t["turn_id"] not in labels), len(turns))

    try:
        while i < len(turns):
            t = turns[i]
            show_turn(turns, i, len(labels), len(turns))
            existing = labels.get(t["turn_id"])
            key = prompt(existing)
            if key == "q":
                break
            if key == "b":
                i = max(0, i - 1)
                continue
            if key != "keep":
                note = input("Note (optional, Enter to skip): ").strip()
                labels[t["turn_id"]] = {"label": LABELS[key], "note": note, "at": now()}
                save_labels(out, data)
            i += 1
    except (KeyboardInterrupt, EOFError):
        print()
    save_labels(out, data)

    left = len(turns) - len(labels)
    if left:
        print(f"\nSaved {len(labels)}/{len(turns)} labels to {out}. Run the same command to continue.")
    else:
        print(f"\nAll {len(turns)} turns labeled. Saved to {out}.")


if __name__ == "__main__":
    main()
