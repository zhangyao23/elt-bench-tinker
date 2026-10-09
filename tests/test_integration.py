"""Credential-free tests use real CSV extraction, DuckDB execution and pinned grading."""

import json

import pytest
from sqlglot.errors import ParseError

from elt_tinker.environment import Episode
from elt_tinker.local_fixture import GOOD_SQL, load_all, local_task
from elt_tinker.warehouse import prepare_sql


def test_full_execution_feedback_replay_and_reward(tmp_path):
    task = local_task(tmp_path / "task")
    episode = Episode(task, "duckdb", tmp_path / "run")
    try:
        assert "Hidden reference" in episode.initial_prompt()
        assert "summary.csv" not in episode.initial_prompt()
        load_all(episode)
        assert not episode.act("transform", {"sql": GOOD_SQL.replace("o.amount", "o.nonexistent")})[
            "ok"
        ]
        assert episode.act("query", {"sql": "SELECT * FROM raw.orders"})["ok"]
        assert episode.act("transform", {"sql": GOOD_SQL})["ok"]
        # Destroy interactive output outside the agent boundary. Fresh replay must repair it.
        episode.warehouse.execute("DROP TABLE models.summary")
        assert episode.act("submit_pipeline", {})["result"]["reward"] == 1
        grade = json.loads((tmp_path / "run/grade.json").read_text())
        assert grade["all_models_match"] and grade["raw"]["orders"]["match"]
        assert grade["official"] is False
        with pytest.raises(ValueError, match="ended"):
            episode.act("submit_pipeline", {})
    finally:
        episode.close()


def test_wrong_pipeline_has_execution_derived_partial_reward(tmp_path):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run")
    try:
        load_all(episode)
        assert episode.act(
            "transform", {"sql": GOOD_SQL.replace("COALESCE(SUM(o.amount), 0)", "0")}
        )["ok"]
        episode.act("submit_pipeline", {})
        assert episode.grade["reward"] == 0.75
        assert not episode.grade["all_models_match"]
        assert episode.grade["models"]["summary"]["unmatched"] == ["total"]
    finally:
        episode.close()


def test_duplicate_query_columns_allow_model_to_repair(tmp_path):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run")
    try:
        load_all(episode)
        result = episode.act("query", {"sql": "SELECT 1 AS id, 2 AS ID"})
        assert not result["ok"] and result["error_kind"] == "action"
        assert not episode.done and episode.invalid_reason is None
        assert episode.act("query", {"sql": "SELECT 1 AS id, 2 AS other_id"})["ok"]
        assert episode.act("transform", {"sql": GOOD_SQL})["ok"]
        assert episode.act("submit_pipeline", {})["result"]["reward"] == 1
    finally:
        episode.close()


def test_missing_load_and_non_submission_fail_closed(tmp_path):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run")
    try:
        assert episode.evaluate()["reward"] == 0
        assert episode.act("submit_pipeline", {})["result"]["reward"] == 0
    finally:
        episode.close()


@pytest.mark.parametrize(
    "sql,transform",
    [
        ("COPY raw.orders TO '/tmp/out.csv'", False),
        ("SELECT * FROM read_csv('hidden/summary.csv')", False),
        ("SELECT * FROM information_schema.tables", False),
        ("SELECT * FROM otherdb.raw.orders", False),
        ("SELECT getenv('TINKER_API_KEY')", False),
        ("SELECT 1; DROP TABLE raw.orders", False),
        ("CREATE OR REPLACE TABLE orders AS SELECT 1 AS amount", True),
        ("CREATE TABLE raw.orders AS SELECT 1", True),
        ("INSERT INTO models.summary VALUES (1, 'Alice', 30, 2)", True),
        ("SELECT * INTO OUTFILE '/tmp/data' FROM raw.orders", False),
    ],
)
def test_sql_boundary(sql, transform):
    with pytest.raises((ValueError, ParseError)):
        prepare_sql(sql, "duckdb", {"orders"}, {"summary"}, "raw", "models", transform=transform)


def test_episodes_do_not_share_warehouse_or_pipeline(tmp_path):
    task = local_task(tmp_path / "task")
    first = Episode(task, "duckdb", tmp_path / "first")
    second = Episode(task, "duckdb", tmp_path / "second")
    try:
        load_all(first)
        assert not second.act("query", {"sql": "SELECT * FROM raw.orders"})["ok"]
        assert second.configured == {} and second.sql == []
    finally:
        first.close()
        second.close()
