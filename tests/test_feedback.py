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
