"""Real local execution and Snowflake-dialect parsing; no model/cloud training."""

import json

import pandas as pd
import pytest
import sqlglot

from elt_tinker.environment import Episode, feedback_value
from elt_tinker.local_fixture import GOOD_SQL, load_all, local_task
from elt_tinker.warehouse import prepare_sql


@pytest.mark.parametrize("target", ["summary", "models.summary", "MODELS.summary"])
def test_logical_output_target_executes_and_replays(tmp_path, target):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run")
    try:
        load_all(episode)
        result = episode.act("transform", {"sql": GOOD_SQL.replace("TABLE summary", f"TABLE {target}")})
        assert result["ok"] and result["state"]["successful_transform_statements"] == 1
        assert not result["state"]["submitted"]
        assert episode.act("submit_pipeline", {})["result"]["reward"] == 1
    finally:
        episode.close()


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE other.summary AS SELECT 1 AS id",
        "CREATE TABLE otherdb.models.summary AS SELECT 1 AS id",
        "CREATE TABLE models.orders AS SELECT 1 AS id",
        "CREATE TABLE raw.summary AS SELECT 1 AS id",
        "CREATE TABLE models.summary AS SELECT * FROM read_csv('secret.csv')",
        "CREATE TABLE models.summary AS SELECT EXISTS(SELECT 1 FROM hidden.gold) AS id",
        "CREATE TABLE models.summary AS SELECT FIRST_VALUE(id) OVER () FROM other.summary",
        "CREATE TABLE models.summary AS SELECT TO_DATE(getenv('TINKER_API_KEY')) AS id",
    ],
)
def test_new_sql_support_preserves_policy_boundary(tmp_path, sql):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run")
    try:
        result = episode.act("transform", {"sql": sql})
        assert not result["ok"] and result["error_kind"] == "action"
        assert result["error_category"] == "sql_policy"
        assert not episode.sql and not episode.done
    finally:
        episode.close()


def test_date_window_and_exists_retain_actual_values(tmp_path):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run")
    try:
        load_all(episode)
        episode.warehouse.load("dates", pd.DataFrame({"day": ["2017-01-01", None]}))
        prepared = prepare_sql(
            "SELECT TO_DATE(day) AS day FROM raw.dates",
            "snowflake", {"dates"}, {"summary"}, "raw", "models", transform=False,
        )
        # Test the pinned Snowflake AST path without claiming a Snowflake server query.
        converted = sqlglot.transpile(prepared, read="snowflake", write="duckdb")[0]
        dates = episode.warehouse.query(converted)
        assert dates.iloc[0, 0] == pd.Timestamp("2017-01-01")
        assert pd.isna(dates.iloc[1, 0])
        result = episode.act("query", {"sql": "SELECT FIRST_VALUE(amount) OVER (PARTITION BY customer_id ORDER BY amount DESC) AS largest FROM raw.orders"})
        assert result["ok"]
        assert sorted(row["largest"] for row in result["result"]["rows"]) == [5, 5, 20, 20]
        result = episode.act("query", {"sql": "SELECT EXISTS(SELECT 1 FROM raw.orders WHERE amount > 10) AS found"})
        assert result["result"]["rows"] == [{"found": True}]
    finally:
        episode.close()


def test_query_dates_types_nulls_and_budget_state(tmp_path):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run", max_calls=2)
    try:
        result = episode.act("query", {"sql": "SELECT CAST('2017-01-01' AS DATE) AS day, CAST('2017-01-01 12:34:56' AS TIMESTAMP) AS stamp, CAST(NULL AS DATE) AS missing"})
        feedback = result["result"]
        assert feedback["rows"] == [{"day": "2017-01-01T00:00:00", "stamp": "2017-01-01T12:34:56", "missing": None}]
        assert feedback["column_types"] == {"day": "DATE", "stamp": "TIMESTAMP", "missing": "DATE"}
        assert result["state"]["tool_calls_remaining"] == 1
        assert not result["state"]["ended"]
        last = episode.act("query", {"sql": "SELECT 1 AS value"})
        assert last["state"]["tool_calls_remaining"] == 0 and last["state"]["ended"]
        assert not last["state"]["submitted"]
        assert episode.evaluate()["reward"] == 0
        saved = json.loads((tmp_path / "run/tool_trace.json").read_text(encoding="utf-8"))
        assert saved[-1]["feedback"]["state"] == last["state"]
    finally:
        episode.close()


def test_date_feedback_preserves_offset_and_naive_time():
    aware = pd.Timestamp("2017-01-01 12:00:00", tz="America/New_York")
    naive = pd.Timestamp("2017-01-01 12:00:00")
    assert feedback_value(aware) == "2017-01-01T12:00:00-05:00"
    assert feedback_value(naive) == "2017-01-01T12:00:00"
    assert feedback_value(pd.NaT) is None


@pytest.mark.parametrize("enabled", [False, True])
def test_field_feedback_is_optional_and_does_not_change_reward(tmp_path, enabled):
    episode = Episode(
        local_task(tmp_path / "task"), "duckdb", tmp_path / "run", field_feedback=enabled,
    )
    try:
        load_all(episode)
        calls = []
        original_query = episode.warehouse.query

        def counted_query(sql):
            calls.append(sql)
            return original_query(sql)

        episode.warehouse.query = counted_query
        result = episode.act("transform", {"sql": GOOD_SQL.replace("AS total", "AS sum_total")})
        assert result["ok"] and result["result"]["executed"]
        assert episode.grade is None and not episode.submitted
        if enabled:
            schema = result["result"]["output_schema"]
            assert schema["missing_columns"] == ["total"]
            assert schema["extra_columns"] == ["sum_total"]
            assert not schema["coverage_complete"] and not schema["values_verified"]
            assert len(calls) == 2 and "LIMIT 0" in calls[-1]
            assert episode.timings[-1]["phase"] == "field_feedback"
        else:
            assert "output_schema" not in result["result"] and len(calls) == 1
        assert episode.act("submit_pipeline", {})["result"]["reward"] == 0.75
    finally:
        episode.close()


def test_field_feedback_uses_public_columns_not_hidden_reference(tmp_path):
    from elt_tinker.task import Task

    class ForbiddenReference(dict):
        def __getitem__(self, key):
            raise AssertionError("Feedback read hidden reference")

        def get(self, *args):
            raise AssertionError("Feedback read hidden reference")

    task = local_task(tmp_path / "task")
    episode = Episode(task, "duckdb", tmp_path / "run", field_feedback=True)
    try:
        load_all(episode)
        episode.task = Task(task.public, task.hidden, task.spec, ForbiddenReference())
        result = episode.act("transform", {"sql": GOOD_SQL.replace("COALESCE(SUM(o.amount), 0)", "0")})
        schema = result["result"]["output_schema"]
        assert schema["coverage_complete"] and not schema["values_verified"]
        assert schema["required_columns"] == ["id", "name", "total", "num_orders"]
        assert not any(name in json.dumps(result) for name in ["summary.csv", "sha256", "reward"])
        episode.task = task
        assert episode.act("submit_pipeline", {})["result"]["reward"] == 0.75
    finally:
        episode.close()


def test_unavailable_schema_does_not_erase_successful_transformation(tmp_path, monkeypatch):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run", field_feedback=True)
    try:
        load_all(episode)
        query = episode.warehouse.query

        def fail_inspection(sql):
            if sql.startswith("SELECT"):
                raise ValueError("unit fault: metadata unavailable")
            return query(sql)

        monkeypatch.setattr(episode.warehouse, "query", fail_inspection)
        result = episode.act("transform", {"sql": GOOD_SQL})
        assert result["ok"] and result["result"]["executed"]
        assert not result["result"]["output_schema"]["available"]
        assert episode.sql == [GOOD_SQL] and not episode.done
        monkeypatch.setattr(episode.warehouse, "query", query)
        assert episode.act("submit_pipeline", {})["result"]["reward"] == 1
    finally:
        episode.close()


def test_schema_infrastructure_failure_invalidates_without_fake_reward(tmp_path, monkeypatch):
    from elt_tinker.errors import InfrastructureError

    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run", field_feedback=True)
    try:
        load_all(episode)
        query = episode.warehouse.query

        def fail_inspection(sql):
            if sql.startswith("SELECT"):
                raise InfrastructureError("unit fault: connection lost")
            return query(sql)

        monkeypatch.setattr(episode.warehouse, "query", fail_inspection)
        result = episode.act("transform", {"sql": GOOD_SQL})
        assert not result["ok"] and result["error_kind"] == "infrastructure" and episode.done
        assert episode.sql == [GOOD_SQL]
        grade = episode.evaluate()
        assert not grade["valid"] and grade["reward"] is None
    finally:
        episode.close()


def test_field_feedback_without_public_columns_does_not_infer_gold(tmp_path):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run", field_feedback=True)
    try:
        load_all(episode)
        episode.task.spec["models"][0].pop("columns")
        result = episode.act("transform", {"sql": GOOD_SQL})
        assert result["ok"] and not result["result"]["output_schema"]["available"]
        assert not any(entry["phase"] == "field_feedback" for entry in episode.timings)
    finally:
        episode.close()


def test_cookbook_and_recipe_expose_field_feedback_without_cloud_calls(tmp_path, monkeypatch):
    import asyncio

    pytest.importorskip("tinker_cookbook")
    from elt_tinker.cookbook_env import ELTGroupBuilder
    from elt_tinker.recipe import arguments

    monkeypatch.setattr("sys.argv", [
        "elt-train", "--mode", "rollout", "--model", "unit-model",
        "--public", "public", "--hidden", "hidden", "--destination", "duckdb",
        "--output", "run", "--max-cost-usd", "1", "--field-feedback",
    ])
    assert arguments().field_feedback
    builder = ELTGroupBuilder(
        local_task(tmp_path / "task"), "duckdb", tmp_path / "run", None, 1, {},
        field_feedback=True,
    )
    try:
        episode = builder._create_episode(0)
        load_all(episode)
        result = episode.act("transform", {"sql": GOOD_SQL})
        assert result["result"]["output_schema"]["coverage_complete"]
    finally:
        asyncio.run(builder.cleanup())


def test_empty_output_has_schema_but_is_not_task_success(tmp_path):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run", field_feedback=True)
    try:
        load_all(episode)
        sql = "CREATE TABLE models.summary AS SELECT id, name, 0 AS total, 0 AS num_orders FROM raw.customers WHERE 1 = 0"
        result = episode.act("transform", {"sql": sql})
        schema = result["result"]["output_schema"]
        assert schema["coverage_complete"] and not schema["values_verified"]
        assert result["state"]["created_models"] == ["summary"]
        assert episode.act("submit_pipeline", {})["result"]["reward"] == 0
        assert not episode.grade["all_models_match"]
    finally:
        episode.close()


def test_repeated_transform_counts_statements_and_unique_models(tmp_path):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run", field_feedback=True)
    try:
        load_all(episode)
        for count in (1, 2):
            result = episode.act("transform", {"sql": GOOD_SQL})
            assert result["state"]["successful_transform_statements"] == count
            assert result["state"]["created_models"] == ["summary"]
            assert result["result"]["output_schema"]["coverage_complete"]
            assert not result["result"]["output_schema"]["values_verified"]
        assert not episode.submitted and episode.grade is None
        assert episode.act("submit_pipeline", {})["result"]["reward"] == 1
    finally:
        episode.close()
