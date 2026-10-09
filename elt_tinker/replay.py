"""Replay saved configuration and SQL in a new process, independently of the original Episode."""

import argparse
import hashlib
from pathlib import Path

from elt_tinker.environment import Episode, safe_error
from elt_tinker.task import Task, read_json


def replay(task: Task, pipeline: dict, output: Path, operator_config: dict | None = None):
    if pipeline.get("version") != 1 or pipeline["task_id"] != task.spec["id"]:
        raise ValueError("Pipeline version/task mismatch")
    if (
        pipeline["task_sha256"]
        != hashlib.sha256((task.public / "task.json").read_bytes()).hexdigest()
    ):
        raise ValueError("Pipeline task digest mismatch")
    output.mkdir(parents=True, exist_ok=False)
    episode = Episode(task, pipeline["destination"], output, operator_config)
    try:
        actions = [
            ("configure_load", {"table": table, **config})
            for table, config in pipeline["load"].items()
        ]
        actions += [
            ("run_load", {}),
            *[("transform", {"sql": sql}) for sql in pipeline["transform_sql"]],
        ]
        for name, arguments in actions:
            result = episode.act(name, arguments)
            if not result["ok"]:
                raise ValueError(result["error"])
        episode.act("submit_pipeline", {})
        return episode.grade
    finally:
        episode.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--hidden", type=Path, required=True)
    parser.add_argument("--pipeline", type=Path, required=True)
    parser.add_argument("--operator-config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = replay(
            Task.load(args.public, args.hidden),
            read_json(args.pipeline),
            args.output,
            read_json(args.operator_config) if args.operator_config else None,
        )
        print(f"Replay valid={report['valid']} reward={report['reward']}")
        if not report["valid"] or not report.get("all_models_match"):
            raise SystemExit(1)
    except Exception as error:
        raise SystemExit(safe_error(error)) from None


if __name__ == "__main__":
    main()
