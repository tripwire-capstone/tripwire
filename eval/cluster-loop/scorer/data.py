"""One shape for every source of clusters: a SystemView per system (Tripwire or Laminar).

Sources: a compare-laminar raw.json (both systems, live at comparison time), or a restored run
database (Tripwire's stored clusters, or a replay's {event_id: cluster_id}).

An Event's `clusters` is a tuple of cluster keys, () when unclustered:
  * Tripwire: one cluster at most (cluster_members.event_id is unique).
  * Laminar: every named entry at the event's lowest level >= 1 of `cluster_details` (level 0 is
    Laminar's internal unnamed grouping, so an event with only level 0 is unclustered). Usually
    one; a few events that describe two problems sit in two level-1 clusters, and both are kept
    (decided Nov 18 with the user: metric B counts both; metric A picks one, see `single`).
Signals are keyed by lower-cased name ("User frustration" vs Laminar's "User Frustration").
"""

import json
from dataclasses import dataclass, field

TRIPWIRE = "tripwire"
LAMINAR = "laminar"


@dataclass(frozen=True)
class Event:
    event_id: object            # int (Tripwire) or str (Laminar)
    trace_id: str               # the Laminar trace UUID as text: the join key between systems
    clusters: tuple = ()        # cluster keys; () = unclustered

    @property
    def single(self):
        """The one cluster used where a metric needs exactly one (answer key, coverage sizes):
        the lowest str() of the event's clusters, None if unclustered."""
        return min(self.clusters, key=str) if self.clusters else None


@dataclass
class SystemView:
    system: str
    signals: dict = field(default_factory=dict)        # signal key -> display name
    events: dict = field(default_factory=dict)         # signal key -> [Event]
    cluster_names: dict = field(default_factory=dict)  # cluster key -> name

    def add(self, signal_name, event):
        key = signal_key(signal_name)
        self.signals.setdefault(key, signal_name)
        self.events.setdefault(key, []).append(event)


def signal_key(name):
    return name.strip().lower()


def laminar_clusters(cluster_details):
    """The cluster ids at the lowest level >= 1 that have a non-empty name, sorted by str()."""
    named = [entry for entry in cluster_details or () if entry["level"] >= 1 and entry.get("name")]
    if not named:
        return ()
    lowest = min(entry["level"] for entry in named)
    return tuple(sorted({entry["id"] for entry in named if entry["level"] == lowest}, key=str))


def load_raw(path):
    return json.loads(open(path).read())


def tripwire_from_raw(raw):
    data = raw["tripwire"]
    cluster_of = {row["event_id"]: row["cluster_id"] for row in data["member_clusters"]}
    view = SystemView(TRIPWIRE, cluster_names={row["id"]: row["name"] for row in data["all_clusters"]})
    for event in sorted(data["events"], key=lambda row: row["id"]):
        cluster = cluster_of.get(event["id"])
        view.add(event["signal_name"], Event(event["id"], str(event["laminar_trace_id"]),
                                             () if cluster is None else (cluster,)))
    return view


def laminar_from_raw(raw):
    view = SystemView(LAMINAR)
    for event in sorted(raw["laminar"]["events"], key=lambda row: row["id"]):
        for entry in event.get("cluster_details") or ():
            if entry["level"] >= 1 and entry.get("name"):
                view.cluster_names[entry["id"]] = entry["name"]
        view.add(event["signal_name"], Event(event["id"], str(event["trace_id"]),
                                             laminar_clusters(event.get("cluster_details"))))
    return view


DB_EVENTS_SQL = """
SELECT sig_event.id, trace.laminar_trace_id, signal.name, member.cluster_id
FROM signal_events sig_event
JOIN signal_versions version ON version.id = sig_event.signal_version_id
JOIN signals signal ON signal.id = version.signal_id
JOIN traces trace ON trace.id = sig_event.trace_id
LEFT JOIN cluster_members member ON member.event_id = sig_event.id
ORDER BY sig_event.id
"""


def tripwire_from_db(conn, membership=None, names=None):
    """Every event in the run's database. `membership` ({event_id: cluster_id or None}, a replay)
    replaces the stored clusters; events missing from it (never embedded) are unclustered.
    `names` ({cluster_id: name}) for a replay; otherwise the stored names are read."""
    view = SystemView(TRIPWIRE)
    for event_id, trace_id, signal_name, stored_cluster in conn.execute(DB_EVENTS_SQL):
        cluster = stored_cluster if membership is None else membership.get(event_id)
        view.add(signal_name, Event(event_id, str(trace_id), () if cluster is None else (cluster,)))
    if names is not None:
        view.cluster_names = dict(names)
    elif membership is None:
        view.cluster_names = dict(conn.execute("SELECT id, name FROM clusters"))
    return view


def compare_stored_to_raw(db_view, raw_view):
    """The dump's stored clusters vs Tripwire's raw.json side: should be identical. Returns a
    dict of differences (empty lists = identical)."""
    def table(view):
        return {event.event_id: (key, event.single) for key, events in view.events.items() for event in events}
    db, raw = table(db_view), table(raw_view)
    only_db = sorted(set(db) - set(raw))
    only_raw = sorted(set(raw) - set(db))
    moved = sorted(event_id for event_id in set(db) & set(raw) if db[event_id] != raw[event_id])
    return {"events_db": len(db), "events_raw": len(raw), "only_in_db": only_db[:20],
            "only_in_raw": only_raw[:20], "n_only_in_db": len(only_db), "n_only_in_raw": len(only_raw),
            "cluster_differs": moved[:20], "n_cluster_differs": len(moved),
            "identical": not (only_db or only_raw or moved)}
