import hashlib
from pathlib import Path

import pandas as pd

from elt_tinker.environment import Episode
from elt_tinker.task import Task, write_json


def local_task(root: Path) -> Task:
    public, hidden = root / "public", root / "hidden"
    public.mkdir(parents=True)
    hidden.mkdir(parents=True)
    # Deliberately exercise nulls, missing activity, ties and duplicate orders.
    pd.DataFrame({"id": [1, 2, 3], "name": ["Alice", "Bob", "Cai"]}).to_csv(
        public / "customers.csv", index=False
    )
    pd.DataFrame({"customer_id": [1, 1, 2, 2], "amount": [10, 20, 5, None]}).to_csv(
        public / "orders.csv", index=False
    )
    pd.DataFrame(
        {
            "id": [1, 2, 3],
            "name": ["Alice", "Bob", "Cai"],
            "total": [30, 5, 0],
            "num_orders": [2, 2, 0],
        }
    ).to_csv(hidden / "summary.csv", index=False)
    sources = {
        "customers": {
            "kind": "file",
            "path": "customers.csv",
            "format": "csv",
            "columns": ["id", "name"],
        },
        "orders": {
            "kind": "file",
            "path": "orders.csv",
            "format": "csv",
            "columns": ["customer_id", "amount"],
        },
    }
    write_json(
        public / "task.json",
        {
            "id": "local_sales",
            "official": False,
            "sources": sources,
            "models": [
                {
                    "name": "summary",
                    "description": "Each customer, total ordered amount (0 without orders), num_orders; retain all customers.",
                }
            ],
        },
    )
    write_json(
        hidden / "reference.json",
        {
            "source_counts": {"customers": 3, "orders": 4},
            "tables": {
                "summary": {
                    "csv": "summary.csv",
                    "keys": ["id"],
                    "sha256": hashlib.sha256((hidden / "summary.csv").read_bytes()).hexdigest(),
                }
            },
        },
    )
    return Task.load(public, hidden)


GOOD_SQL = """CREATE OR REPLACE TABLE summary AS
SELECT c.id, c.name, COALESCE(SUM(o.amount), 0) AS total, COUNT(o.customer_id) AS num_orders
FROM raw.customers c LEFT JOIN raw.orders o ON c.id = o.customer_id
GROUP BY c.id, c.name"""


def load_all(episode: Episode):
    for table in episode.task.spec["sources"]:
        assert episode.act("configure_load", {"table": table, "source_kind": "file"})["ok"]
    assert episode.act("run_load", {})["ok"]
