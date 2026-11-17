"""Step 4 with an LLM: name new clusters, and rename clusters that have grown (SIG-65, SIG-66).

Decisions: "Signals Prototype: Design Decisions (Phase 2)", section "Cluster quality".
Runs instead of naming.name_new_clusters unless NAMING_MODEL=off; naming.py is untouched and
still the fallback: when the LLM can't name a NEW cluster, it gets exactly the name naming.py
would have given it (the summary of its member closest to the center).

The pieces, in call order (one pass of step 4):

  find_work       two reads: unnamed clusters (to name) and named clusters with their sizes;
                  renames_due (pure) picks which named ones have grown enough to rename
  load_input      three reads for one cluster: its Signal, its members (closest to the center
                  first) and the names its sibling clusters already have
  pick_sample     pure: which members the LLM sees (8 closest + 3 farthest, summarized only)
  build_messages  pure: the system prompt (prompts/name_vN.md) + one user message for the cluster
  ask_llm         one paid call (llm_client.py) for one cluster, then clean_name (pure): the
                  name as stored, or LLMBadOutput if it isn't usable
  save            one short transaction: re-checks the Signal's names for duplicates, then writes
                  the name only if nobody changed the cluster's name since it was read
  name_clusters   one pass of step 4: every job through load_input -> ask_llm -> save, with the
                  failure rules below. Namer wraps it with its settings for loop.run_pass.

When the LLM can't name a cluster (llm.LLMError: bad or unusable output, refused, too long, or
the service is down): a NEW cluster gets the fallback (its closest member's summary, marked
'fallback', retried at its next doubling); a RENAME keeps the current name (named_member_count
unchanged, so it's due again next pass). After a service error (timeout, rate limit, outage) no
more calls are made this pass: the rest of the new clusters get their fallback at once and the
renames wait, so an outage can't hang a pass for a timeout per cluster. Any other error (a wrong
key, a bad model name, a bug) is not caught: the step fails loudly and loop.py logs it (Q20).

No LLM call happens inside a database transaction: every read ends before the call, and each
write is its own short transaction that checks nothing changed meanwhile.
"""

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from .naming import NAME_CANDIDATES_SQL, unique_name
from .sql import leaf_cluster
from ..llm import LLMBadOutput, LLMError, LLMServiceError
from .summarize import PROMPTS, Prompt

log = logging.getLogger("clusterer.naming")


@dataclass(frozen=True)
class Job:
    """One cluster to name or rename this pass."""
    cluster_id: int
    signal_id: int
    kind: str                         # "new" (no name yet) or "rename" (grown since last named)
    fallback: str | None = None       # new only: the closest member's summary (naming.py's name)
    current_name: str | None = None   # rename only
    current_source: str | None = None  # rename only: 'llm', 'fallback', or None (old step 4)
    named_at: datetime | None = None  # rename only: the write checks it hasn't changed


# Every named leaf cluster with its size: how many members, and how many have a finished summary.
# Which of them are due for a rename is decided in Python (renames_due), not here.
NAMED_CLUSTERS_SQL = f"""
SELECT named.id, named.signal_id, named.name, named.name_source, named.named_at,
       named.named_member_count,
       count(*) AS members,
       count(summary.event_id) AS summarized
FROM clusters named
JOIN cluster_members member ON member.cluster_id = named.id
LEFT JOIN event_summaries summary ON summary.event_id = member.event_id AND summary.status = 'done'
WHERE named.name IS NOT NULL AND {leaf_cluster("named")} AND named.centroid IS NOT NULL
GROUP BY named.id
"""


@dataclass(frozen=True)
class NamedCluster:
    """One row of NAMED_CLUSTERS_SQL."""
    cluster_id: int
    signal_id: int
    name: str
    source: str | None                # 'llm', 'fallback', or None (named by the old step 4)
    named_at: datetime | None
    named_member_count: int | None    # None = never named by llm_naming
    members: int
    summarized: int


def rename_is_due(cluster, growth):
    """Never named by llm_naming, or grown to `growth` x its size when last named (rounded up).
    Decimal, not float: 1.1 x 50 is exactly 55, where the float 55.00000000000001 would wait for 56."""
    if cluster.named_member_count is None:
        return True
    threshold = math.ceil(Decimal(str(growth)) * cluster.named_member_count)
    return cluster.members >= threshold


def renames_due(clusters, growth, max_renames):
    """The clusters to rename this pass, at most `max_renames`, most overdue first:
    old-step names first (one member's sentence: the worst names we have), then by how much the
    cluster has grown since named (x4 before x2), then the older cluster.
    A cluster with no summarized member is skipped: there'd be nothing to show the LLM, and it
    would take a slot every pass (placeholder mode, test data)."""
    due = [cluster for cluster in clusters
           if cluster.summarized > 0 and rename_is_due(cluster, growth)]

    def most_overdue_first(cluster):
        if cluster.named_member_count is None:
            return (0, 0, cluster.cluster_id)
        growth_so_far = Fraction(cluster.members, cluster.named_member_count)
        return (1, -growth_so_far, cluster.cluster_id)

    return sorted(due, key=most_overdue_first)[:max_renames]


def find_work(conn, growth, max_renames):
    """This pass's jobs: every unnamed cluster that can be named, then up to `max_renames`
    renames. New clusters come in ID order, so when two want the same name in one Signal the
    older one gets it plain (as naming.py does)."""
    new = [Job(cluster_id, signal_id, "new", fallback=summary)
           for cluster_id, signal_id, summary in conn.execute(NAME_CANDIDATES_SQL).fetchall()]
    named = [NamedCluster(*row) for row in conn.execute(NAMED_CLUSTERS_SQL).fetchall()]
    renames = [Job(cluster.cluster_id, cluster.signal_id, "rename", current_name=cluster.name,
                   current_source=cluster.source, named_at=cluster.named_at)
               for cluster in renames_due(named, growth, max_renames)]
    return new + renames


# ------------------------------------------------------------------ load_input, pick_sample

CLOSEST_MEMBERS = 8   # the cluster's core: what most of it is about
FARTHEST_MEMBERS = 3  # its edges: so the name covers the whole cluster, not just the core


@dataclass(frozen=True)
class Member:
    event_id: int
    summary: str | None               # None = no finished summary (placeholder mode, test data)
    details: tuple                    # "kind: value" strings; () for v1 summaries or none found


@dataclass(frozen=True)
class ClusterInput:
    """Everything the prompt for one cluster is built from."""
    job: Job
    signal_name: str
    instruction: str                  # from the Signal's latest version
    member_count: int                 # every member, summarized or not
    members: tuple                    # every member, closest to the center first
    sibling_names: tuple              # names of the Signal's other clusters, oldest cluster first


# The Signal as it is now: its latest version's instruction, even if some members were judged
# under an older one (decided Nov 16: the latest is what the Signal means today).
SIGNAL_SQL = """
SELECT signal.name, version.instruction
FROM signals signal
JOIN signal_versions version ON version.signal_id = signal.id
WHERE signal.id = %s
ORDER BY version.version_number DESC
LIMIT 1
"""

# Every member, closest to the cluster's CURRENT center first (cluster_members.distance is the
# distance when the event joined; the center has moved since). Ties go to the lower event ID.
MEMBERS_SQL = """
SELECT member.event_id, summary.summary, summary.details
FROM clusters cluster
JOIN cluster_members member ON member.cluster_id = cluster.id
JOIN event_embeddings embedded ON embedded.event_id = member.event_id
LEFT JOIN event_summaries summary ON summary.event_id = member.event_id AND summary.status = 'done'
WHERE cluster.id = %s
ORDER BY embedded.embedding <=> cluster.centroid, member.event_id
"""

SIBLING_NAMES_SQL = f"""
SELECT sibling.name
FROM clusters sibling
WHERE sibling.signal_id = %s AND sibling.id <> %s AND sibling.name IS NOT NULL
  AND {leaf_cluster("sibling")}
ORDER BY sibling.id
"""


def load_input(conn, job):
    """The prompt's inputs for one job, or None if the cluster has no members any more (every
    trace deleted since find_work): nothing to name, so the job is skipped."""
    rows = conn.execute(MEMBERS_SQL, (job.cluster_id,)).fetchall()
    if not rows:
        return None
    members = tuple(Member(event_id, summary, tuple(details or ()))
                    for event_id, summary, details in rows)
    signal_name, instruction = conn.execute(SIGNAL_SQL, (job.signal_id,)).fetchone()
    siblings = conn.execute(SIBLING_NAMES_SQL, (job.signal_id, job.cluster_id)).fetchall()
    return ClusterInput(job, signal_name, instruction, member_count=len(members), members=members,
                        sibling_names=tuple(name for (name,) in siblings))


def pick_sample(members):
    """(closest, farthest): the summarized members the LLM sees, each list closest-first.
    `members` must already be closest-first (load_input's order). A cluster of 11 or fewer
    summarized members is shown whole; its last few simply land in `farthest`."""
    summarized = [member for member in members if member.summary is not None]
    closest = summarized[:CLOSEST_MEMBERS]
    farthest = summarized[CLOSEST_MEMBERS:][-FARTHEST_MEMBERS:]
    return closest, farthest


# ------------------------------------------------------------------ the prompt

# Like the summarizer's prompts: a change, even one word, is a new file with the next number
# (prompts/README.md). Names don't record which version wrote them, so try_naming is where
# versions get compared.
NAMING_PROMPT_VERSION = 1


def naming_prompt_paths(version):
    return PROMPTS / f"name_v{version}.md", PROMPTS / f"name_v{version}.schema.json"


def load_naming_prompt(version=NAMING_PROMPT_VERSION):
    text_path, schema_path = naming_prompt_paths(version)
    return Prompt(version, text_path.read_text().strip(), json.loads(schema_path.read_text()))


def build_messages(cluster, system_prompt):
    """The two messages for one cluster: the system prompt, then the cluster as tagged sections.
    The current name is shown only when it's an LLM name: an old-step or fallback name is one
    member's sentence, and showing it would anchor the model to exactly what we're replacing."""
    closest, farthest = pick_sample(cluster.members)
    shown = len(closest) + len(farthest)

    cluster_lines = [f"size: {cluster.member_count} findings ({shown} shown below)"]
    job = cluster.job
    if job.kind == "rename" and job.current_source == "llm":
        cluster_lines.append(f"current name: {job.current_name}")

    sections = [
        _section("tripwire_signal", [f"name: {cluster.signal_name}",
                                     f"instruction: {cluster.instruction}"]),
        _section("tripwire_cluster", cluster_lines),
        _section("tripwire_closest_members", _member_lines(closest)),
    ]
    if farthest:
        sections.append(_section("tripwire_farthest_members", _member_lines(farthest)))
    sections.append(_section("tripwire_other_names",
                             [f"- {name}" for name in cluster.sibling_names] or ["(none)"]))
    return [{"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n\n".join(sections)}]


def _section(tag, lines):
    return f"<{tag}>\n" + "\n".join(lines) + f"\n</{tag}>"


def _member_lines(members):
    """One numbered line per member; its details on the next line ("; " between them, because a
    detail has a colon of its own)."""
    lines = []
    for number, member in enumerate(members, start=1):
        lines.append(f"{number}. {member.summary}")
        if member.details:
            lines.append("   details: " + "; ".join(member.details))
    return lines


# ------------------------------------------------------------------ the call and the answer

MAX_NAME_WORDS = 12   # the prompt asks for 3-8; a 9-word name that's otherwise fine is kept
MAX_NAME_CHARS = 80   # a name is a list label, not a sentence
QUOTES = "\"'\u201c\u201d\u2018\u2019`"   # straight, curly and back quotes


def clean_name(output):
    """The model's answer -> the name to store. Raises LLMBadOutput when it isn't usable: the
    caller then treats it like any other bad answer (fallback for a new cluster, keep the name
    for a rename). Tidies only what a model adds around a name; never changes its words."""
    raw = output.get("name") if isinstance(output, dict) else None
    if not isinstance(raw, str):
        raise LLMBadOutput(f"answer has no name: {output!r}")
    name = " ".join(raw.split())          # trims, and turns newlines and runs of spaces into one
    name = name.strip(QUOTES).strip()
    name = name.rstrip(".").strip()
    if not name:
        raise LLMBadOutput(f"name is empty: {raw!r}")
    if len(name.split()) > MAX_NAME_WORDS or len(name) > MAX_NAME_CHARS:
        raise LLMBadOutput(f"name is too long ({len(name.split())} words, {len(name)} characters): "
                           f"{name!r}")
    return name


def ask_llm(llm, model, cluster, prompt):
    """One paid call: (name, usage). Raises any LLMError (llm_client.py's four, or LLMBadOutput
    from clean_name); anything else (a wrong key, a bad model name, a bug) raises as it is."""
    result = llm.complete(model, build_messages(cluster, prompt.system), prompt.schema)
    try:
        return clean_name(result.output), result.usage
    except LLMBadOutput as error:
        raise LLMBadOutput(str(error), usage=result.usage) from error


# ------------------------------------------------------------------ save

# A new cluster is written only if it's still unnamed; a rename only if its name and named_at
# are still what find_work read (named_at alone can't tell for an old-step name: it's NULL
# before and after an old-step write). 0 rows = someone else named it first: skipped, not
# overwritten.
NAME_NEW_SQL = """
UPDATE clusters
SET name = %(name)s, name_source = %(source)s, named_at = now(),
    named_member_count = %(count)s, updated_at = now()
WHERE id = %(cluster_id)s AND name IS NULL
"""

RENAME_SQL = """
UPDATE clusters
SET name = %(name)s, name_source = %(source)s, named_at = now(),
    named_member_count = %(count)s, updated_at = now()
WHERE id = %(cluster_id)s
  AND name IS NOT DISTINCT FROM %(old_name)s
  AND named_at IS NOT DISTINCT FROM %(old_named_at)s
"""


def save(conn, job, name, source, member_count):
    """Write one cluster's name. Returns the name as stored (with "(2)" etc. if another
    cluster of the Signal has it), or None if the cluster was named by someone else since it
    was read. `member_count` is the size the name describes: counted when the input was read,
    not now (members may have joined during the call; the next doubling is measured from it)."""
    with conn.transaction():
        # Re-read now, not the names the model saw: other clusters may have been named since.
        # The cluster's own current name isn't among them, so keeping a name never adds "(2)".
        taken = {sibling for (sibling,) in
                 conn.execute(SIBLING_NAMES_SQL, (job.signal_id, job.cluster_id)).fetchall()}
        stored = unique_name(name, taken)
        values = {"name": stored, "source": source, "count": member_count,
                  "cluster_id": job.cluster_id, "old_name": job.current_name,
                  "old_named_at": job.named_at}
        written = conn.execute(NAME_NEW_SQL if job.kind == "new" else RENAME_SQL, values).rowcount
    if written == 0:
        log.info("cluster %s was named by someone else meanwhile; left as it is", job.cluster_id)
        return None
    return stored


# ------------------------------------------------------------------ one pass of step 4

@dataclass
class NamingResult:
    named: int = 0          # new clusters, LLM name
    fallback: int = 0       # new clusters, fallback name (the LLM couldn't)
    renamed: int = 0        # renames written (an unchanged name counts too: it's re-recorded)
    kept: int = 0           # renames the LLM couldn't do: current name kept, retried next pass
    skipped: int = 0        # named by someone else meanwhile, or no members left
    service_down: bool = False  # a service error stopped the calls for this pass

    def written(self):
        return self.named + self.fallback + self.renamed

    def anything_happened(self):
        return bool(self.written() or self.kept or self.skipped)


def name_clusters(conn, llm, model, prompt, growth, max_renames):
    """One pass of step 4. Returns a NamingResult; raises only for non-LLM errors."""
    result = NamingResult()
    for job in find_work(conn, growth, max_renames):
        cluster = load_input(conn, job)
        if cluster is None:
            result.skipped += 1
            continue

        name = None
        if not result.service_down:
            try:
                name, _ = ask_llm(llm, model, cluster, prompt)
            except LLMServiceError as error:
                result.service_down = True
                log.warning("naming: OpenAI unavailable (%s); no more naming calls this pass", error)
            except LLMError as error:
                log.warning("naming: cluster %s: %s: %s", job.cluster_id, type(error).__name__, error)

        if name is not None:
            source = "llm"
        elif job.kind == "new":
            name, source = job.fallback, "fallback"
        else:
            result.kept += 1
            continue

        stored = save(conn, job, name, source, cluster.member_count)
        if stored is None:
            result.skipped += 1
        elif job.kind == "rename":
            result.renamed += 1
        elif source == "llm":
            result.named += 1
        else:
            result.fallback += 1
    return result


class Namer:
    """Step 4 with its settings, as loop.run_pass calls it: name(conn) -> clusters written.
    Built once at startup (like Preparer), so the OpenAI client and the prompt are reused."""

    def __init__(self, llm, model, growth, max_renames, prompt=None):
        self.llm = llm
        self.model = model
        self.growth = growth
        self.max_renames = max_renames
        self.prompt = prompt or load_naming_prompt()

    def __call__(self, conn):
        result = name_clusters(conn, self.llm, self.model, self.prompt, self.growth,
                               self.max_renames)
        if result.anything_happened():
            log.info("naming: %d named, %d fallback, %d renamed, %d kept, %d skipped%s",
                     result.named, result.fallback, result.renamed, result.kept, result.skipped,
                     " (stopped calling after a service error)" if result.service_down else "")
        return result.written()
