"""Tests for llm_naming.py (SIG-65/66): LLM cluster names, fallback, renames on growth."""

import pytest

from tripwire.clusterer.llm_client import FakeLLM
from tripwire.llm import LLMBadOutput, LLMServiceError

from tests.clustering_data import (
    add_cluster,
    add_event,
    add_member,
    add_signal,
    add_summary,
    add_version,
)
from tests.vectors import base_vector, vector_at
from tripwire.clusterer.llm_naming import (
    NAMED_CLUSTERS_SQL,
    NAMING_PROMPT_VERSION,
    ClusterInput,
    Job,
    Member,
    NamedCluster,
    ask_llm,
    build_messages,
    clean_name,
    find_work,
    load_naming_prompt,
    load_input,
    pick_sample,
    rename_is_due,
    renames_due,
)


def member(conn, version_id, cluster_id, distance=0.1, axis=1, summary="a summary"):
    """An event in `cluster_id`, `distance` from the base vector, with a done summary (or none)."""
    event_id = add_event(conn, version_id, vector_at(distance, axis))
    add_member(conn, event_id, cluster_id)
    if summary is not None:
        add_summary(conn, event_id, summary)
    return event_id


def cluster_with(conn, signal_id, version_id, size, name=None, source=None, named_count=None):
    """A leaf cluster with `size` summarized members, optionally already named."""
    cluster_id = add_cluster(conn, signal_id, base_vector())
    for index in range(size):
        member(conn, version_id, cluster_id, axis=index + 1, summary=f"summary {index}")
    if name is not None:
        conn.execute(
            """UPDATE clusters SET name = %s, name_source = %s, named_member_count = %s,
                   named_at = CASE WHEN %s::text IS NULL THEN NULL ELSE now() END
               WHERE id = %s""",
            (name, source, named_count, source, cluster_id),
        )
    return cluster_id


def renames(jobs):
    return [job.cluster_id for job in jobs if job.kind == "rename"]


# ------------------------------------------------------------------ find_work: new clusters

def test_an_unnamed_cluster_is_new_work_with_its_closest_summary_as_fallback(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    member(conn, version_id, cluster_id, distance=0.2, axis=1, summary="farther")
    member(conn, version_id, cluster_id, distance=0.05, axis=2, summary="closest")

    assert find_work(conn, growth=2.0, max_renames=10) == [
        Job(cluster_id, signal_id, "new", fallback="closest")]


def test_an_unnamed_cluster_with_no_summarized_member_is_left_for_later(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    member(conn, version_id, cluster_id, summary=None)
    assert find_work(conn, growth=2.0, max_renames=10) == []


def test_new_clusters_come_in_id_order_before_renames(conn):
    signal_id, version_id = add_signal(conn)
    old_step_named = cluster_with(conn, signal_id, version_id, 3, name="Old name")
    first = cluster_with(conn, signal_id, version_id, 3)
    second = cluster_with(conn, signal_id, version_id, 3)

    jobs = find_work(conn, growth=2.0, max_renames=10)
    assert [(job.cluster_id, job.kind) for job in jobs] == [
        (first, "new"), (second, "new"), (old_step_named, "rename")]


# ------------------------------------------------------------------ rename_is_due / renames_due (pure)

def named(cluster_id=1, members=4, named_count=None, summarized=None, source="llm"):
    """A NamedCluster as NAMED_CLUSTERS_SQL returns it. named_count=None = named by the old step."""
    return NamedCluster(cluster_id, signal_id=1, name="Some Name",
                        source=None if named_count is None else source, named_at=None,
                        named_member_count=named_count, members=members,
                        summarized=members if summarized is None else summarized)


@pytest.mark.parametrize("growth, named_count, members, due", [
    (2.0, 4, 7, False),     # one short of double
    (2.0, 4, 8, True),      # exactly double
    (1.5, 5, 7, False),     # 7.5 rounds up to 8
    (1.5, 5, 8, True),
    (1.1, 50, 55, True),    # 1.1 x 50 as a float is 55.00000000000001: must not wait for 56
    (1.1, 50, 54, False),
])
def test_rename_is_due_at_growth_times_the_named_size(growth, named_count, members, due):
    assert rename_is_due(named(members=members, named_count=named_count), growth) is due


def test_a_name_from_the_old_step_is_due_whatever_the_size():
    assert rename_is_due(named(members=3, named_count=None), growth=2.0)


def test_a_fallback_name_is_due_at_the_same_doubling_as_an_llm_name():
    for source in ("llm", "fallback"):
        assert not rename_is_due(named(members=5, named_count=3, source=source), growth=2.0)
        assert rename_is_due(named(members=6, named_count=3, source=source), growth=2.0)


def test_renames_are_capped_most_overdue_first():
    tripled = named(1, members=6, named_count=2)
    doubled = named(2, members=4, named_count=2)
    old_step = named(3, members=3, named_count=None)
    quadrupled = named(4, members=8, named_count=2)
    picked = renames_due([tripled, doubled, old_step, quadrupled], growth=2.0, max_renames=3)
    assert [c.cluster_id for c in picked] == [3, 4, 1]


def test_equally_overdue_renames_go_oldest_cluster_first():
    picked = renames_due([named(7, members=4, named_count=2), named(5, members=6, named_count=3)],
                         growth=2.0, max_renames=1)
    assert [c.cluster_id for c in picked] == [5]


def test_a_cap_of_zero_means_no_renames():
    assert renames_due([named(members=3, named_count=None)], growth=2.0, max_renames=0) == []


def test_a_cluster_with_no_summarized_member_is_never_due():
    assert renames_due([named(members=3, named_count=None, summarized=0)], 2.0, 10) == []


# ------------------------------------------------------------------ find_work: renames (database)

def test_named_clusters_are_read_with_members_and_summarized_counts(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = cluster_with(conn, signal_id, version_id, 2, name="Old name")
    member(conn, version_id, cluster_id, axis=9, summary=None)    # a member with no summary
    [row] = conn.execute(NAMED_CLUSTERS_SQL).fetchall()
    assert NamedCluster(*row) == NamedCluster(cluster_id, signal_id, "Old name", None, None, None,
                                              members=3, summarized=2)


def test_a_name_from_the_old_step_becomes_a_rename_job_with_its_current_name(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = cluster_with(conn, signal_id, version_id, 3, name="Old step's summary")
    assert find_work(conn, growth=2.0, max_renames=10) == [
        Job(cluster_id, signal_id, "rename", current_name="Old step's summary")]


def test_a_grown_llm_name_becomes_a_rename_job_and_an_ungrown_one_does_not(conn):
    signal_id, version_id = add_signal(conn)
    grown = cluster_with(conn, signal_id, version_id, 4, name="A", source="llm", named_count=2)
    cluster_with(conn, signal_id, version_id, 3, name="B", source="llm", named_count=2)
    assert renames(find_work(conn, growth=2.0, max_renames=10)) == [grown]


def test_parent_clusters_and_clusters_without_a_center_are_never_work(conn):
    signal_id, version_id = add_signal(conn)
    parent = add_cluster(conn, signal_id, base_vector(), level=1)
    no_center = add_cluster(conn, signal_id, None)
    for cluster_id in (parent, no_center):
        member(conn, version_id, cluster_id)
    assert find_work(conn, growth=2.0, max_renames=10) == []
    conn.execute("UPDATE clusters SET name = 'Old name'")
    assert find_work(conn, growth=2.0, max_renames=10) == []


# ------------------------------------------------------------------ pick_sample (pure)

def members(count, unsummarized=()):
    """`count` members, closest-first, event IDs 1..count; IDs in `unsummarized` have no summary."""
    return [Member(i, None if i in unsummarized else f"summary {i}", ()) for i in range(1, count + 1)]


def ids(sample):
    closest, farthest = sample
    return [m.event_id for m in closest], [m.event_id for m in farthest]


def test_a_small_cluster_is_shown_whole_as_closest():
    assert ids(pick_sample(members(3))) == ([1, 2, 3], [])


def test_eleven_members_are_shown_whole_none_twice():
    assert ids(pick_sample(members(11))) == ([1, 2, 3, 4, 5, 6, 7, 8], [9, 10, 11])


def test_a_big_cluster_shows_its_8_closest_and_3_farthest():
    assert ids(pick_sample(members(20))) == ([1, 2, 3, 4, 5, 6, 7, 8], [18, 19, 20])


def test_members_without_a_summary_are_left_out_even_the_closest():
    assert ids(pick_sample(members(12, unsummarized={1, 12}))) == (
        [2, 3, 4, 5, 6, 7, 8, 9], [10, 11])


def test_no_summarized_members_gives_an_empty_sample():
    assert ids(pick_sample(members(3, unsummarized={1, 2, 3}))) == ([], [])


# ------------------------------------------------------------------ load_input (database)

def new_job(cluster_id, signal_id):
    return Job(cluster_id, signal_id, "new", fallback="unused")


def test_members_come_closest_to_the_current_center_first(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    far = member(conn, version_id, cluster_id, distance=0.3, axis=1)
    near = member(conn, version_id, cluster_id, distance=0.05, axis=2)
    middle = member(conn, version_id, cluster_id, distance=0.15, axis=3)
    conn.execute("UPDATE cluster_members SET distance = 0.9 WHERE event_id = %s", (near,))  # stale
    loaded = load_input(conn, new_job(cluster_id, signal_id))
    assert [m.event_id for m in loaded.members] == [near, middle, far]


def test_members_at_the_same_distance_come_in_event_id_order(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    first = member(conn, version_id, cluster_id, distance=0.1, axis=1)
    second = member(conn, version_id, cluster_id, distance=0.1, axis=2)
    loaded = load_input(conn, new_job(cluster_id, signal_id))
    assert [m.event_id for m in loaded.members] == [first, second]


def test_member_count_includes_members_without_a_summary(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    member(conn, version_id, cluster_id, axis=1)
    unsummarized = member(conn, version_id, cluster_id, axis=2, summary=None)
    loaded = load_input(conn, new_job(cluster_id, signal_id))
    assert loaded.member_count == 2
    assert [m.summary for m in loaded.members if m.event_id == unsummarized] == [None]


def test_details_are_passed_through_and_missing_details_are_empty(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    with_details = member(conn, version_id, cluster_id, distance=0.05, axis=1)
    member(conn, version_id, cluster_id, distance=0.2, axis=2)       # a v1 summary: details NULL
    conn.execute("""UPDATE event_summaries SET details = '["park: Glacier Bay", "tool: book_campsite"]'
                    WHERE event_id = %s""", (with_details,))
    loaded = load_input(conn, new_job(cluster_id, signal_id))
    assert [m.details for m in loaded.members] == [("park: Glacier Bay", "tool: book_campsite"), ()]


def test_the_instruction_comes_from_the_signals_latest_version(conn):
    signal_id, version_1 = add_signal(conn, name="Booking problems")
    cluster_id = add_cluster(conn, signal_id, base_vector())
    member(conn, version_1, cluster_id)                               # judged under version 1
    version_2 = add_version(conn, signal_id, 2)
    conn.execute("UPDATE signal_versions SET instruction = 'Flag failed bookings.' WHERE id = %s",
                 (version_2,))
    loaded = load_input(conn, new_job(cluster_id, signal_id))
    assert (loaded.signal_name, loaded.instruction) == ("Booking problems", "Flag failed bookings.")


def test_sibling_names_are_the_signals_other_named_leaf_clusters(conn):
    signal_id, version_id = add_signal(conn)
    other_signal, _ = add_signal(conn, name="Signal B")
    cluster_id = add_cluster(conn, signal_id, base_vector())
    member(conn, version_id, cluster_id)
    for name, sig, level in [("Older Sibling", signal_id, 0), (None, signal_id, 0),
                             ("Other Signal's", other_signal, 0), ("A Parent", signal_id, 1),
                             ("Newer Sibling", signal_id, 0)]:
        sibling = add_cluster(conn, sig, base_vector(), level=level)
        conn.execute("UPDATE clusters SET name = %s WHERE id = %s", (name, sibling))
    conn.execute("UPDATE clusters SET name = 'Its Own Name' WHERE id = %s", (cluster_id,))
    loaded = load_input(conn, new_job(cluster_id, signal_id))
    assert loaded.sibling_names == ("Older Sibling", "Newer Sibling")


def test_a_cluster_whose_members_are_all_gone_gives_none(conn):
    signal_id, _ = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    assert load_input(conn, new_job(cluster_id, signal_id)) is None


# ------------------------------------------------------------------ build_messages (pure)

def cluster_input(member_count=3, job=None, siblings=(), details=()):
    job = job or Job(7, 1, "new", fallback="unused")
    shown = tuple(Member(i, f"summary {i}", details) for i in range(1, member_count + 1))
    return ClusterInput(job, "Issues", "Flag mistakes.", member_count, shown, tuple(siblings))


def user_message(cluster):
    system, user = build_messages(cluster, "THE SYSTEM PROMPT")
    assert system == {"role": "system", "content": "THE SYSTEM PROMPT"}
    assert user["role"] == "user"
    return user["content"]


def test_the_message_has_the_signal_cluster_members_and_other_names_in_order():
    text = user_message(cluster_input(siblings=["Older Name"]))
    assert text == (
        "<tripwire_signal>\nname: Issues\ninstruction: Flag mistakes.\n</tripwire_signal>\n\n"
        "<tripwire_cluster>\nsize: 3 findings (3 shown below)\n</tripwire_cluster>\n\n"
        "<tripwire_closest_members>\n1. summary 1\n2. summary 2\n3. summary 3\n"
        "</tripwire_closest_members>\n\n"
        "<tripwire_other_names>\n- Older Name\n</tripwire_other_names>")


def test_farthest_members_appear_only_for_clusters_bigger_than_eight():
    assert "<tripwire_farthest_members>" not in user_message(cluster_input(8))
    text = user_message(cluster_input(20))
    assert "size: 20 findings (11 shown below)" in text
    assert "<tripwire_farthest_members>\n1. summary 18\n2. summary 19\n3. summary 20\n" in text


def test_no_other_clusters_says_none():
    assert "<tripwire_other_names>\n(none)\n</tripwire_other_names>" in user_message(cluster_input())


def test_details_go_on_their_own_line_separated_by_semicolons():
    text = user_message(cluster_input(1, details=("item: sneaker", "tool: create_exchange")))
    assert "1. summary 1\n   details: item: sneaker; tool: create_exchange\n" in text


@pytest.mark.parametrize("job, shown", [
    (Job(7, 1, "rename", current_name="Wrong Item Shipped", current_source="llm"), True),
    (Job(7, 1, "rename", current_name="one member's summary", current_source="fallback"), False),
    (Job(7, 1, "rename", current_name="one member's summary", current_source=None), False),
    (Job(7, 1, "new", fallback="one member's summary"), False),
])
def test_the_current_name_is_shown_only_when_it_is_an_llm_name(job, shown):
    text = user_message(cluster_input(job=job))
    assert ("current name: Wrong Item Shipped" in text) is shown
    assert "one member's summary" not in text


def test_the_naming_prompt_files_exist_and_ask_for_just_a_name():
    prompt = load_naming_prompt()
    assert prompt.version == NAMING_PROMPT_VERSION
    assert "Title Case" in prompt.system
    assert prompt.schema["required"] == ["name"]
    assert prompt.schema["additionalProperties"] is False


# ------------------------------------------------------------------ clean_name, ask_llm (pure)

@pytest.mark.parametrize("raw, cleaned", [
    ("Delivery Date Change Requests", "Delivery Date Change Requests"),
    ("  Delivery Date   Change\nRequests  ", "Delivery Date Change Requests"),
    ('"Delivery Date Change Requests"', "Delivery Date Change Requests"),
    ("\u201cDelivery Date Change Requests.\u201d", "Delivery Date Change Requests"),
    ("Delivery Date Change Requests.", "Delivery Date Change Requests"),
    ("U.S. Address Errors", "U.S. Address Errors"),          # inner periods are kept
    ("Can't Find Order Status", "Can't Find Order Status"),  # inner apostrophes are kept
])
def test_clean_name_tidies_only_what_surrounds_the_name(raw, cleaned):
    assert clean_name({"name": raw}) == cleaned


@pytest.mark.parametrize("output", [
    {"name": ""}, {"name": "   "}, {"name": '"."'}, {"name": None}, {}, "not a dict",
])
def test_clean_name_rejects_an_answer_with_no_usable_name(output):
    with pytest.raises(LLMBadOutput):
        clean_name(output)


def test_clean_name_allows_twelve_words_but_not_thirteen():
    assert clean_name({"name": " ".join(["Word"] * 12)})
    with pytest.raises(LLMBadOutput, match="13 words"):
        clean_name({"name": " ".join(["Word"] * 13)})


def test_clean_name_allows_eighty_characters_but_not_eighty_one():
    assert clean_name({"name": "x" * 80})
    with pytest.raises(LLMBadOutput, match="81 characters"):
        clean_name({"name": "x" * 81})


def test_ask_llm_sends_the_prompt_and_returns_the_cleaned_name_and_usage():
    llm = FakeLLM([{"name": " Delivery Date Change Requests. "}])
    prompt = load_naming_prompt()
    name, usage = ask_llm(llm, "test-model", cluster_input(), prompt)
    assert (name, usage) == ("Delivery Date Change Requests", llm.usage)
    [request] = llm.requests
    assert request["model"] == "test-model"
    assert request["json_schema"] == prompt.schema
    assert request["messages"] == build_messages(cluster_input(), prompt.system)


def test_ask_llm_reports_an_unusable_name_as_bad_output_with_its_usage():
    llm = FakeLLM([{"name": ""}])
    with pytest.raises(LLMBadOutput) as raised:
        ask_llm(llm, "test-model", cluster_input(), load_naming_prompt())
    assert raised.value.usage == llm.usage          # the call was paid for: keep its cost


def test_ask_llm_passes_llm_errors_and_other_errors_through():
    for error in (LLMServiceError("down"), ValueError("a bug")):
        with pytest.raises(type(error)):
            ask_llm(FakeLLM([error]), "test-model", cluster_input(), load_naming_prompt())
