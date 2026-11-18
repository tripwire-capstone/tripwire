"""scores.json / scores.md for `score run`, and the aggregates-only view for `score gate`."""

from statistics import mean


def _num(value, digits=2):
    return "-" if value is None else f"{value:.{digits}f}"


def _prf(scores):
    if not scores:
        return "-"
    return f"{_num(scores['precision'])} / {_num(scores['recall'])} / {_num(scores['f05'])}"


# ------------------------------------------------------------------ tuning-set averages

def averages(results, view="replay"):
    """Per signal: the mean over runs of each headline number (runs lacking it are skipped)."""
    collected = {}
    for result in results:
        for key, entry in (result.get(view) or {}).items():
            bucket = collected.setdefault(key, {"name": entry["name"]})
            answer = (entry.get("answer_key") or {}).get("tripwire")
            if answer:
                for metric in ("precision", "recall", "f05"):
                    bucket.setdefault(f"answer_key_{metric}", []).append((result["run"], answer[metric]))
            agreement = entry.get("agreement")
            if agreement and agreement["precision"] is not None:
                for metric in ("precision", "recall"):
                    bucket.setdefault(f"agreement_{metric}", []).append((result["run"], agreement[metric]))
            bucket.setdefault("pct_unclustered", []).append(
                (result["run"], entry["coverage"]["tripwire"]["pct_unclustered"]))
    out = {}
    for key, bucket in collected.items():
        out[key] = {"name": bucket.pop("name")}
        for metric, values in bucket.items():
            out[key][metric] = {"mean": mean(value for _, value in values), "runs": [run for run, _ in values]}
    return out


# ------------------------------------------------------------------ scores.md

def render_markdown(meta, results, avg):
    lines = [f"# Cluster scores ({meta['created_utc']})", "",
             f"- git commit `{meta['git_commit']}`, working tree {'clean' if meta['git_clean'] else 'DIRTY'}",
             f"- scorer sha256 `{meta['scorer_hash']}`",
             f"- command: `{meta['command']}`", ""]
    if avg:
        lines += ["## Tuning-set averages (replay)", "",
                  "| signal | answer key P / R / F0.5 | agreement P / R | TW % unclustered | runs |",
                  "|---|---|---|---|---|"]
        for key, entry in sorted(avg.items()):
            answer = "-" if "answer_key_f05" not in entry else " / ".join(
                _num(entry[f"answer_key_{metric}"]["mean"]) for metric in ("precision", "recall", "f05"))
            agreement = "-" if "agreement_precision" not in entry else " / ".join(
                _num(entry[f"agreement_{metric}"]["mean"]) for metric in ("precision", "recall"))
            runs = sorted({run for metric in entry.values() if isinstance(metric, dict) for run in metric["runs"]})
            lines.append(f"| {entry['name']} | {answer} | {agreement} | {_num(entry['pct_unclustered']['mean'], 1)} | "
                         f"{', '.join(runs)} |")
        lines += ["", "Averages include every run listed, whatever its summarizer version (see each run's notes).", ""]
    for result in results:
        lines += render_run(result)
    return "\n".join(lines) + "\n"


def render_run(result):
    title = f"## {result['run']} ({result['kind']}, threshold {result['threshold']}"
    title += f", as v{result['as_version']})" if result["as_version"] else ", summaries as stored)"
    lines = [title, "", f"_{result['notes']}_", ""]
    if result.get("costs"):
        lines += [f"Paid calls: {result['costs']}", ""]
    if result.get("as_version_not_summarized"):
        lines += [f"**{len(result['as_version_not_summarized'])} events got no v{result['as_version']} summary** "
                  "(unclustered in the replay).", ""]
    check = result.get("stored_vs_raw")
    if check is not None:
        if check["identical"]:
            lines += [f"Stored clusters in the dump match Tripwire's raw.json side ({check['events_db']} events).", ""]
        else:
            lines += [f"**Stored clusters differ from raw.json:** {check['n_only_in_db']} events only in the DB, "
                      f"{check['n_only_in_raw']} only in raw.json, {check['n_cluster_differs']} in a different "
                      f"cluster (first ids: {check['cluster_differs'][:10]}).", ""]
    key_check = result.get("answer_key_vs_raw")
    if key_check is not None and not key_check["identical"]:
        lines += [f"**Answer key file and raw.json's copy differ** ({key_check}).", ""]
    for view, label in (("replay", "Replay (working-tree code)"), ("live", "Live, as captured in raw.json")):
        if result.get(view):
            lines += [f"### {label}", ""] + render_view(result[view], result["kind"], view) + [""]
    if result.get("naming"):
        lines += ["### Naming (replayed clusters, real namer)", ""] + render_naming(result["naming"]) + [""]
    return lines


def render_view(view, kind, which="live"):
    """`which` = "replay": Laminar isn't replayed, so its columns are its live numbers (labeled so)."""
    lines = []
    lm = "LM (live)" if which == "replay" else "LM"
    if kind == "park":
        lines += ["Answer key (BCubed):", "",
                  f"| signal | labels | TW P / R / F0.5 | TW n labeled / events | {lm} P / R / F0.5 | {lm} n labeled / events |",
                  "|---|---|---|---|---|---|"]
        for key, entry in sorted(view.items()):
            answer = entry.get("answer_key")
            if not answer:
                lines.append(f"| {entry['name']} | (counts only) | - | - | - | - |")
                continue
            tripwire, laminar = answer["tripwire"], answer.get("laminar")
            lines.append(f"| {entry['name']} | {entry['label_mode']} | {_prf(tripwire)} | "
                         f"{tripwire['n_labeled']} / {tripwire['n_events']} | {_prf(laminar)} | "
                         + (f"{laminar['n_labeled']} / {laminar['n_events']} |" if laminar else "- |"))
        lines.append("")
    if any(entry.get("agreement") for entry in view.values()):
        lines += ["Laminar agreement (trace level):", "",
                  "| signal | shared | scored | TW-unclustered among scored | TW-tied among scored | agreement P / R |",
                  "|---|---|---|---|---|---|"]
        for key, entry in sorted(view.items()):
            agreement = entry.get("agreement")
            if agreement:
                lines.append(f"| {entry['name']} | {agreement['n_shared']} | {agreement['n_scored']} | "
                             f"{agreement['n_tripwire_unclustered']} | {agreement.get('n_tripwire_tied', 0)} | "
                             f"{_num(agreement['precision'])} / "
                             f"{_num(agreement['recall'])} |")
        lines.append("")
    lines += ["Coverage (events):", "",
              "| signal | system | events | clusters | unclustered | % unclustered | largest cluster share |",
              "|---|---|---|---|---|---|---|"]
    for key, entry in sorted(view.items()):
        for system, cov in entry["coverage"].items():
            shown = "laminar (live)" if system == "laminar" and which == "replay" else system
            lines.append(f"| {entry['name']} | {shown} | {cov['n_events']} | {cov['n_clusters']} | "
                         f"{cov['n_unclustered']} | {_num(cov['pct_unclustered'], 1)} | {_num(cov['largest_share'])} |")
    lines.append("")
    for key, entry in sorted(view.items()):
        agreement = entry.get("agreement")
        if agreement and agreement["mapping"]:
            lines += [f"Mapping, {entry['name']} (Laminar cluster (scored traces) -> Tripwire cluster holding most):", ""]
            lines += [f"- {row['laminar_name']!r} ({row['n_traces']}) -> {row['tripwire_name']!r} {row['n_shared']}"
                      for row in agreement["mapping"]]
            lines.append("")
    return lines


def render_naming(naming):
    lines = ["| signal | clusters named | fallbacks | D1 keyword | D2 rubric | D3 Laminar name match |",
             "|---|---|---|---|---|---|"]
    for key, entry in sorted(naming.items()):
        keyword, grade, match = entry["keyword"], entry["grade"], entry["laminar_match"]
        lines.append(f"| {key} | {entry['n_named']} | {entry['n_fallback']} | "
                     + (f"{_num(keyword['score'])} ({keyword['n_clusters']} clusters)" if keyword else "-") + " | "
                     + (f"{_num(grade['score'])} ({grade['n_clusters']})" if grade else "not run") + " | "
                     + (f"{_num(match['score'])} ({match['n_pairs']} pairs)" if match else "-") + " |")
    lines.append("")
    for key, entry in sorted(naming.items()):
        if entry["keyword"] and entry["keyword"]["rows"]:
            lines += [f"Keyword check, {key}:", ""]
            lines += [f"- {'OK ' if row['match'] else 'MISS'} {row['cause']} ({row['share']:.0%}): {row['name']!r}"
                      for row in entry["keyword"]["rows"]]
            lines.append("")
        if entry["laminar_match"] and entry["laminar_match"]["rows"]:
            lines += [f"Name match, {key}:", ""]
            lines += [f"- {row['cosine']:.2f}  {row['laminar']!r} ~ {row['tripwire']!r}"
                      for row in entry["laminar_match"]["rows"]]
            lines.append("")
    return lines


# ------------------------------------------------------------------ gate: aggregates only

def gate_aggregates(result):
    """Only numbers and counts from the replay: no cluster ids, names, summaries or event ids."""
    out = {}
    for key, entry in (result.get("replay") or {}).items():
        row = {}
        answer = (entry.get("answer_key") or {}).get("tripwire")
        if answer:
            row["answer_key"] = {metric: round(answer[metric], 4) for metric in ("precision", "recall", "f05")}
            row["answer_key"].update(n_labeled=answer["n_labeled"], n_events=answer["n_events"])
        agreement = entry.get("agreement")
        if agreement:
            row["agreement"] = {metric: (None if agreement[metric] is None else round(agreement[metric], 4))
                                for metric in ("precision", "recall")}
            row["agreement"].update(n_shared=agreement["n_shared"], n_scored=agreement["n_scored"],
                                    n_tripwire_unclustered=agreement["n_tripwire_unclustered"],
                                    n_tripwire_tied=agreement.get("n_tripwire_tied", 0))
        coverage = entry["coverage"]["tripwire"]
        row["coverage"] = {"n_events": coverage["n_events"], "n_clusters": coverage["n_clusters"],
                           "pct_unclustered": round(coverage["pct_unclustered"], 2),
                           "largest_share": round(coverage["largest_share"], 4)}
        out[key] = row
    return out
