import asyncio
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pandas as pd
import pytest

from elt_tinker.local_fixture import local_task
from elt_tinker.sources import SourceReader
from elt_tinker.task import Task, normalize_source


def test_real_http_reader_and_pinned_file_reader(tmp_path):
    task = local_task(tmp_path / "task")
    payload = pd.read_csv(task.public / "customers.csv").to_dict(orient="records")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        spec = copy.deepcopy(task.spec)
        spec["sources"]["customers"].update(kind="api", path="/customers")
        task = Task(task.public, task.hidden, spec, task.reference)
        reader = SourceReader(task, {"api_url": f"http://127.0.0.1:{server.server_port}"})
        assert len(reader.extract("customers")) == 3
        assert len(reader.extract("orders")) == 4
        assert len(reader.timings) == 2
        assert len(reader.extract("customers")) == 3
        assert len(reader.timings) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_typed_source_preserves_nulls_and_dates():
    frame = normalize_source(
        pd.DataFrame({"ID": ["1", None], "date": ["2020-01-01", None]}),
        {
            "columns": ["id", "date"],
            "column_types": {"id": "number", "date": "date"},
        },
    )
    assert frame.id.iloc[0] == 1 and pd.isna(frame.id.iloc[1])
    assert frame.date.iloc[0] == pd.Timestamp("2020-01-01")


def test_recipe_budget_and_missing_key_no_service_call(tmp_path, monkeypatch):
    pytest.importorskip("tinker_cookbook")
    from elt_tinker.recipe import run, token_budget

    args = SimpleNamespace(
        mode="train",
        max_batches=3,
        group_size=4,
        max_turns=12,
        context_tokens=8000,
        generation_tokens=1000,
    )
    rates = {"prefill": "$0.33", "sample": "$1.005", "train": "$0.737"}
    assert token_budget(args, rates) == pytest.approx(1.373904)
    monkeypatch.delenv("TINKER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="absent"):
        asyncio.run(run(args))


def test_duplicate_projection_rejected():
    from elt_tinker.warehouse import prepare_sql

    with pytest.raises(ValueError, match="Duplicate"):
        prepare_sql(
            "CREATE TABLE summary AS SELECT id AS id,id AS id FROM raw.orders",
            "duckdb",
            {"orders"},
            {"summary"},
            "raw",
            "models",
            transform=True,
        )


def test_budget_reservations_are_cumulative_and_finite(tmp_path):
    pytest.importorskip("tinker_cookbook")
    from elt_tinker.recipe import reserve_budget

    ledger = tmp_path / "budget.json"
    assert reserve_budget(ledger, 10, 7, tmp_path / "rollout")["remaining_usd"] == 3
    with pytest.raises(ValueError, match="cumulative"):
        reserve_budget(ledger, 10, 4, tmp_path / "train")
    with pytest.raises(ValueError, match="implicitly"):
        reserve_budget(ledger, 20, 1, tmp_path / "retry")
    with pytest.raises(ValueError, match="finite"):
        reserve_budget(ledger, float("nan"), 1, tmp_path / "invalid")
    assert reserve_budget(ledger, 10, 3, tmp_path / "train")["remaining_usd"] == 0


def test_resume_cannot_reuse_a_budget_reservation(tmp_path):
    pytest.importorskip("tinker_cookbook")
    from elt_tinker.recipe import claim_resume

    claim_resume(tmp_path, tmp_path / "first")
    with pytest.raises(FileExistsError):
        claim_resume(tmp_path, tmp_path / "second")


def test_seed_credentials_are_bound_to_local_test_services(monkeypatch):
    import os

    from scripts.seed_sources import configure_local_source_credentials

    monkeypatch.setenv("ELT_MONGODB_URI", "mongodb://remote.example.invalid/production")
    monkeypatch.setenv("ELT_POSTGRES_PASSWORD", "operator-secret")
    configure_local_source_credentials()
    assert "127.0.0.1:58717" in os.environ["ELT_MONGODB_URI"]
    assert os.environ["ELT_POSTGRES_PASSWORD"] == "local_test_only"
