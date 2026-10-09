"""Manual five-source destination preflight; never a model-generated rollout."""

import argparse
import copy
from pathlib import Path

from diagnose_shipping import DRIVERS_SQL, TRUCKS_SQL
from seed_sources import configure_local_source_credentials

from elt_tinker.environment import Episode
from elt_tinker.task import Task, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", choices=["duckdb", "snowflake"], default="duckdb")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    configure_local_source_credentials()
    official = ROOT / "data/official/shipping"
    output = args.output or ROOT / f"runs/native_{args.destination}_shipping_diagnostic"
    if args.destination == "duckdb" and args.output is None:
        output = ROOT / "runs/native_shipping_diagnostic"
    public = output / "public"
    spec = copy.deepcopy(read_json(official / "public/task.json"))
    spec.update(official=False, execution_mode=f"manual_native_sources_{args.destination}")
    write_json(public / "task.json", spec)
    task = Task.load(public, official / "hidden")
    operator = read_json(ROOT / "infra/source_config.example.json")
    operator["http_files"]["driver"] = str((official / "source_snapshot/driver.parquet").resolve())
    episode = Episode(task, args.destination, output / "execution", operator)
    try:
        for table, source in spec["sources"].items():
            result = episode.act("configure_load", {"table": table, "source_kind": source["kind"]})
            if not result["ok"]:
                raise RuntimeError(result["error"])
        result = episode.act("run_load", {})
        if not result["ok"]:
            raise RuntimeError(result["error"])
        for sql in (TRUCKS_SQL, DRIVERS_SQL):
            result = episode.act("transform", {"sql": sql})
            if not result["ok"]:
                raise RuntimeError(result["error"])
        episode.act("submit_pipeline", {})
        grade = episode.grade
        print(
            {
                "mode": spec["execution_mode"],
                "valid": grade["valid"],
                "reward": grade["reward"],
                "all_models_match": grade.get("all_models_match"),
                "official_model_rollout": False,
            }
        )
        if not grade["valid"] or not grade.get("all_models_match"):
            raise SystemExit(1)
    finally:
        episode.close()


if __name__ == "__main__":
    main()
