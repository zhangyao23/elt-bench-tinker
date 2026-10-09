"""Public-only hints and bounded model previews do not change warehouse rewards."""

import copy

import sqlglot

from elt_tinker.environment import Episode
from elt_tinker.feedback import compact_tool_feedback, public_condition_warnings
from elt_tinker.local_fixture import GOOD_SQL, load_all, local_task


def test_compact_view_preserves_full_trace_and_truncation_is_explicit():
    feedback = {
        "ok": True,
        "result": {"columns": ["id"], "rows": [{"id": i} for i in range(30)]},
        "state": {"tool_calls_remaining": 10},
    }
    original = copy.deepcopy(feedback)
    view = compact_tool_feedback(feedback)
    assert view["result"]["rows"] == original["result"]["rows"][:6]
    assert view["result"]["rows_shown"] == 6 and view["result"]["rows_truncated"]
    assert feedback == original and len(feedback["result"]["rows"]) == 30
    assert view["state"] == original["state"]


def test_compact_error_preserves_failure_and_small_aggregate_values():
    full = {"ok": False, "error": "bad SQL " * 200, "error_kind": "action"}
    view = compact_tool_feedback(full)
    assert not view["ok"] and view["error_kind"] == "action"
    assert len(view["error"]) == 600 and view["error_truncated"]
    aggregate = {"ok": True, "result": {"rows": [{"count": 960}, {"count": None}]}}
    view = compact_tool_feedback(aggregate)
    assert view["result"]["rows"] == aggregate["result"]["rows"]
    assert not view["result"]["rows_truncated"]


def test_public_year_hint_ignores_alias_and_accepts_date_literals():
    model = {"columns": [{"name": "shipments_2020", "description": "Shipments in 2020."}]}
    no_filter = sqlglot.parse_one("SELECT COUNT(*) AS shipments_2020 FROM raw.orders")
    hints = public_condition_warnings(model, no_filter)
    assert hints[0]["years_absent_as_sql_literals"] == ["2020"]
    assert hints[0]["public_requirement"] == "Shipments in 2020."
    filtered = sqlglot.parse_one("SELECT COUNT(*) FROM raw.orders WHERE day BETWEEN '2020-01-01' AND '2020-12-31'")
    assert not public_condition_warnings(model, filtered)
    # This legitimate equivalent expression can still trigger a review suggestion.
    equivalent = sqlglot.parse_one("SELECT COUNT(*) FROM raw.orders WHERE YEAR(day) = 2019 + 1")
    assert "not a correctness verdict" in public_condition_warnings(model, equivalent)[0]["review_hint"]


def test_condition_hint_is_nonblocking_and_does_not_add_reward(tmp_path):
    task = local_task(tmp_path / "task")
    task.spec["models"][0]["columns"][-1]["description"] = "Number of orders in 2020."
    episode = Episode(task, "duckdb", tmp_path / "run", field_feedback=True)
    try:
        load_all(episode)
        result = episode.act("transform", {"sql": GOOD_SQL})
        assert result["ok"] and result["result"]["executed"]
        hints = result["result"]["output_schema"]["condition_review"]
        assert hints[0]["column"] == "num_orders"
        assert not episode.submitted and episode.grade is None
        # The fixture's execution grader remains unchanged, regardless of the hint.
        assert episode.act("submit_pipeline", {})["result"]["reward"] == 1
    finally:
        episode.close()


def test_condition_switch_changes_only_public_hints(tmp_path):
    task = local_task(tmp_path / "task")
    task.spec["models"][0]["columns"][-1]["description"] = "Number of orders in 2020."
    schemas, rewards = [], []
    for enabled in (False, True):
        episode = Episode(
            task, "duckdb", tmp_path / str(enabled),
            field_feedback=True, condition_feedback=enabled,
        )
        try:
            load_all(episode)
            response = episode.act("transform", {"sql": GOOD_SQL})
            schema = dict(response["result"]["output_schema"])
            hints = schema.pop("condition_review")
            assert bool(hints) == enabled
            schemas.append(schema)
            rewards.append(episode.act("submit_pipeline", {})["result"]["reward"])
        finally:
            episode.close()
    assert schemas[0] == schemas[1] and rewards == [1, 1]
