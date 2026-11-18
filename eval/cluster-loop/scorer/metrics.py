"""Metrics A-C (D, naming, is in naming.py).

BCubed goes through tripwire.clusterer.report.bcubed (cluster_id None = alone, empty label = left
out), with one exception: in metric B a trace whose Tripwire events tie between clusters is in
every tied cluster (decided Nov 18 with the user), and report.bcubed allows one cluster per item.
For those, `bcubed_multi` below is the same Extended BCubed (Amigó et al. 2009) with several
clusters per item; with one cluster per item it equals report.bcubed exactly (tested), and
agreement() calls report.bcubed whenever no trace has more than one cluster.
"""

from collections import Counter

from tripwire.clusterer.report import LabeledEvent, Scores, bcubed

# Which answer-key labels a signal is scored with (spec, metric A). Others (Intent): counts only.
LABEL_MODE_BY_SIGNAL = {"issues": "turn", "user frustration": "sofar"}
UNCLUSTERED = "unclustered"


def _scores(labeled_events):
    scores = bcubed(labeled_events)
    return {"precision": scores.precision, "recall": scores.recall, "f05": scores.f_half}


def bcubed_multi(items):
    """Extended BCubed for items that can be in several clusters and carry several labels.

    `items`: [(clusters, labels)], both frozensets; an empty `clusters` means alone (a cluster of
    its own); items with empty `labels` are left out (as in report.bcubed). For two items e, e'
    with C = shared clusters and L = shared labels (Amigó et al. 2009, multiplicity):
      precision of e' for e (counted when C is non-empty) = min(|C|, |L|) / |C|
      recall    of e' for e (counted when L is non-empty) = min(|C|, |L|) / |L|
    and each item's precision/recall is the mean over those e' (itself included).
    """
    labeled = [(clusters or frozenset({("alone", index)}), labels)
               for index, (clusters, labels) in enumerate(items) if labels]
    if not labeled:
        return Scores(precision=1.0, recall=1.0)
    precision_total = recall_total = 0.0
    for clusters, labels in labeled:
        precision_parts, recall_parts = [], []
        for other_clusters, other_labels in labeled:
            shared_clusters = len(clusters & other_clusters)
            shared_labels = len(labels & other_labels)
            both = min(shared_clusters, shared_labels)
            if shared_clusters:
                precision_parts.append(both / shared_clusters)
            if shared_labels:
                recall_parts.append(both / shared_labels)
        precision_total += sum(precision_parts) / len(precision_parts)
        recall_total += sum(recall_parts) / len(recall_parts)
    return Scores(precision=precision_total / len(labeled), recall=recall_total / len(labeled))


# ------------------------------------------------------------------ A. answer key

def answer_key_scores(events, labels_by_trace):
    """Per event: label = its trace's answer-key labels; cluster = Event.single (alone if None).
    Events with an empty label are left out of P/R/F0.5 but counted in n_events."""
    labeled = [LabeledEvent(index, 0, None, event.single, labels_by_trace.get(event.trace_id, frozenset()))
               for index, event in enumerate(events)]
    result = _scores(labeled)
    result.update(n_labeled=sum(1 for event in labeled if event.bugs), n_events=len(events),
                  pct_unclustered=_pct(sum(1 for event in events if not event.clusters), len(events)))
    return result


# ------------------------------------------------------------------ B. Laminar agreement

def _most_common(counts):
    """The key with the highest count; ties go to the lowest str(key). None if empty."""
    if not counts:
        return None
    best = max(counts.values())
    return min((key for key, count in counts.items() if count == best), key=str)


def tripwire_trace_clusters(events):
    """{trace_id: frozenset of clusters}: the most common cluster among the trace's clustered
    events; on a tie, EVERY tied cluster (decided Nov 18 with the user: a trace whose findings
    split evenly holds both problems, and an id-based tie-break would let a mere renumbering of
    clusters move the score). Empty set = none of its events is clustered."""
    by_trace = {}
    for event in events:
        by_trace.setdefault(event.trace_id, Counter())
        if event.single is not None:
            by_trace[event.trace_id][event.single] += 1
    result = {}
    for trace_id, counts in by_trace.items():
        best = max(counts.values()) if counts else 0
        result[trace_id] = frozenset(key for key, count in counts.items() if count == best)
    return result


def laminar_trace_labels(events):
    """{trace_id: frozenset of Laminar clusters}: the most common cluster among the trace's
    events, each event counting once for every one of its clusters. A tie between clusters that
    includes a cluster of a two-cluster event keeps every tied cluster (the event is in both:
    decided Nov 18); any other tie goes to the lowest str() as the spec says. Empty set = the
    trace has no named Laminar cluster."""
    votes, multi = {}, {}
    for event in events:
        votes.setdefault(event.trace_id, Counter()).update(event.clusters)
        if len(event.clusters) > 1:
            multi.setdefault(event.trace_id, set()).update(event.clusters)
    labels = {}
    for trace_id, counts in votes.items():
        if not counts:
            labels[trace_id] = frozenset()
            continue
        best = max(counts.values())
        tied = {key for key, count in counts.items() if count == best}
        if len(tied) > 1 and tied & multi.get(trace_id, set()):
            labels[trace_id] = frozenset(tied)
        else:
            labels[trace_id] = frozenset({min(tied, key=str)})
    return labels


def agreement(tripwire_events, laminar_events):
    """Trace-level BCubed with Laminar's cluster(s) as the label and Tripwire's cluster as the
    cluster, over shared traces that Laminar put in a named cluster. Also the mapping table."""
    tripwire_by_trace = tripwire_trace_clusters(tripwire_events)
    laminar_by_trace = laminar_trace_labels(laminar_events)
    shared = sorted(set(tripwire_by_trace) & set(laminar_by_trace))
    scored = [trace_id for trace_id in shared if laminar_by_trace[trace_id]]
    if not scored:
        result = {"precision": None, "recall": None, "f05": None}
    elif all(len(tripwire_by_trace[trace_id]) <= 1 for trace_id in scored):
        result = _scores([LabeledEvent(index, 0, None, next(iter(tripwire_by_trace[trace_id]), None),
                                       laminar_by_trace[trace_id])
                          for index, trace_id in enumerate(scored)])
    else:
        scores = bcubed_multi([(tripwire_by_trace[trace_id], laminar_by_trace[trace_id]) for trace_id in scored])
        result = {"precision": scores.precision, "recall": scores.recall, "f05": scores.f_half}
    result.update(n_shared=len(shared), n_scored=len(scored),
                  n_tripwire_unclustered=sum(1 for trace_id in scored if not tripwire_by_trace[trace_id]),
                  n_tripwire_tied=sum(1 for trace_id in scored if len(tripwire_by_trace[trace_id]) > 1),
                  mapping=mapping_table(scored, tripwire_by_trace, laminar_by_trace))
    return result


def mapping_table(scored, tripwire_by_trace, laminar_by_trace):
    """Per Laminar cluster: its scored traces, and the Tripwire cluster (or "unclustered")
    holding most of them (a trace in two Tripwire clusters counts for each; ties: lowest str).
    Largest Laminar cluster first."""
    by_laminar, traces = {}, Counter()
    for trace_id in scored:
        tripwire = tripwire_by_trace[trace_id] or {UNCLUSTERED}
        for laminar in laminar_by_trace[trace_id]:
            traces[laminar] += 1
            by_laminar.setdefault(laminar, Counter()).update(tripwire)
    rows = []
    for laminar, counts in by_laminar.items():
        top = _most_common(counts)
        rows.append({"laminar_cluster": laminar, "n_traces": traces[laminar],
                     "tripwire_cluster": top, "n_shared": counts[top]})
    return sorted(rows, key=lambda row: (-row["n_traces"], str(row["laminar_cluster"])))


# ------------------------------------------------------------------ C. coverage

def coverage(events):
    """Events, clusters, % unclustered, and the largest cluster's share of all events. A Laminar
    event in two clusters counts toward both clusters' sizes."""
    sizes = Counter(cluster for event in events for cluster in event.clusters)
    unclustered = sum(1 for event in events if not event.clusters)
    return {"n_events": len(events), "n_clusters": len(sizes), "n_unclustered": unclustered,
            "pct_unclustered": _pct(unclustered, len(events)),
            "largest_share": (max(sizes.values()) / len(events)) if sizes and events else 0.0}


def _pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


# ------------------------------------------------------------------ per system / per run

def score_signals(tripwire, laminar=None, answer_keys=None, signals=None):
    """All of A-C for one Tripwire view (live or replay) and, if given, Laminar's live view.

    `answer_keys`: {"turn": {trace: labels}, "sofar": {...}} for park runs, else None.
    `signals`: signal keys to score (default: every Tripwire signal).
    Returns {signal_key: {...}}.
    """
    out = {}
    for key in signals or sorted(tripwire.signals):
        tw_events = tripwire.events.get(key, [])
        entry = {"name": tripwire.signals.get(key, key), "coverage": {"tripwire": coverage(tw_events)}}
        mode = LABEL_MODE_BY_SIGNAL.get(key)
        lm_events = laminar.events.get(key, []) if laminar is not None else None
        if laminar is not None:
            entry["coverage"]["laminar"] = coverage(lm_events)
        if answer_keys is not None:
            if mode is None:
                entry["answer_key"] = None      # counts only (Intent): not scored against bugs
            else:
                entry["label_mode"] = mode
                entry["answer_key"] = {"tripwire": answer_key_scores(tw_events, answer_keys[mode])}
                if laminar is not None:
                    entry["answer_key"]["laminar"] = answer_key_scores(lm_events, answer_keys[mode])
        if laminar is not None:
            entry["agreement"] = agreement(tw_events, lm_events)
        out[key] = entry
    return out
