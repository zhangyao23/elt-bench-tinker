import json
from decimal import Decimal

import pandas as pd
import pytest
from sqlglot.errors import ParseError

from elt_tinker.environment import Episode, safe_error
from elt_tinker.local_fixture import GOOD_SQL, load_all, local_task
from elt_tinker.task import fingerprint
from elt_tinker.upstream.eva_stage2 import check_corretness, sort_by_keys
from elt_tinker.warehouse import prepare_sql


def test_canonical_raw_values():
    first = pd.DataFrame({"v": [1.0, None, 1.0]})
    second = pd.DataFrame({"V": [Decimal("1.000"), Decimal("1"), None]})
    assert fingerprint(first) == fingerprint(second)
    assert fingerprint(first) != fingerprint(second.iloc[:2])
    assert fingerprint(pd.DataFrame({"v": [None]})) != fingerprint(pd.DataFrame({"v": ["<NULL>"]}))
    assert fingerprint(pd.DataFrame({"v": [1]})) != fingerprint(pd.DataFrame({"v": ["1"]}))


def test_source_failure_is_invalid_not_zero_reward(tmp_path):
    task = local_task(tmp_path / "task")
    (task.public / "orders.csv").unlink()
    episode = Episode(task, "duckdb", tmp_path / "run")
    try:
        episode.act("configure_load", {"table": "orders", "source_kind": "file"})
        result = episode.act("run_load", {})
        assert result["error_kind"] == "infrastructure" and episode.done
        report = episode.evaluate()
        assert not report["valid"] and report["reward"] is None
    finally:
        episode.close()


def test_tampered_raw_is_not_erased_by_replay(tmp_path):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run")
    try:
        load_all(episode)
        episode.act("transform", {"sql": GOOD_SQL})
        episode.warehouse.execute("DELETE FROM raw.orders")
        episode.act("submit_pipeline", {})
        assert episode.grade["valid"] and episode.grade["reward"] == 0
    finally:
        episode.close()


@pytest.mark.parametrize("replacement", ["WHERE c.id <> 3 GROUP BY", "GROUP BY"])
def test_official_comparator_parity_and_extra_columns(tmp_path, replacement):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run")
    try:
        load_all(episode)
        sql = GOOD_SQL.replace("GROUP BY", replacement).replace("AS total", "AS total")
        sql = sql.replace("c.id, c.name, COALESCE", "c.id, c.name, 99 AS extra, COALESCE")
        assert episode.act("transform", {"sql": sql})["ok"]
        episode.act("submit_pipeline", {})
        if replacement.startswith("WHERE"):
            assert episode.grade["reward"] == 0
        else:
            assert episode.grade["reward"] == 1
            assert episode.grade["models"]["summary"]["extra_columns"] == ["extra"]
    finally:
        episode.close()


def test_null_duplicate_row_and_numeric_tolerance():
    gold = pd.DataFrame({"id": ["1", "2"], "v": ["100", None]})
    within = pd.DataFrame({"ID": ["2", "1"], "V": [None, "100.5"]})
    assert check_corretness(sort_by_keys(gold, ["id"]), sort_by_keys(within, ["id"]))["match"]
    bad_null = within.copy()
    bad_null.iloc[0, 1] = "0"
    assert not check_corretness(sort_by_keys(gold, ["id"]), sort_by_keys(bad_null, ["id"]))["match"]
    duplicates = pd.concat([gold, gold.iloc[:1]], ignore_index=True)
    assert not check_corretness(gold, duplicates)["match"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_parquet('secret.parquet')",
        "SELECT * FROM sqlite_scan('hidden.db','gt')",
        "SELECT current_setting('home_directory')",
        "SELECT otherdb.functions.sum(1)",
        "WITH x AS (SELECT * FROM hidden.gold) SELECT * FROM x",
        "SELECT * FROM pragma_database_list()",
        "SELECT * FROM raw.orders; SELECT 1",
        "SELECT * FROM https://evil.invalid/file.csv",
    ],
)
def test_sql_io_privilege_bypasses_rejected(sql):
    with pytest.raises((ValueError, ParseError)):
        prepare_sql(sql, "duckdb", {"orders"}, {"summary"}, "raw", "models", transform=False)


def test_limit_terminal_and_secret_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("TINKER_API_KEY", "unit-test-secret-12345")
    assert "unit-test-secret-12345" not in safe_error(ValueError("unit-test-secret-12345"))
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run", max_calls=1)
    try:
        episode.act("inspect_task", {})
        assert episode.done and episode.evaluate()["reward"] == 0
        with pytest.raises(ValueError):
            episode.act("submit_pipeline", {})
    finally:
        episode.close()


def test_reference_digest_prevents_tampering(tmp_path):
    task = local_task(tmp_path / "task")
    (task.hidden / "summary.csv").write_text("id,v\n1,99\n")
    from elt_tinker.task import Task

    with pytest.raises(ValueError, match="integrity"):
        Task.load(task.public, task.hidden)


def test_old_score_cache_is_ignored(tmp_path):
    episode = Episode(local_task(tmp_path / "task"), "duckdb", tmp_path / "run")
    try:
        load_all(episode)
        episode.output.mkdir(parents=True, exist_ok=True)
        (episode.output / "grade.json").write_text(json.dumps({"reward": 1}))
        episode.act("transform", {"sql": GOOD_SQL.replace("COALESCE(SUM(o.amount), 0)", "0")})
        episode.act("submit_pipeline", {})
        assert episode.grade["reward"] == 0.75
    finally:
        episode.close()
