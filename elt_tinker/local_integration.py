"""Auditable local end-to-end run. This is not an official rollout or model training."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from elt_tinker.environment import Episode
from elt_tinker.task import write_json


def main():
    # Reuse the same exercised fixture/pipeline rather than maintain a second demo.
    from elt_tinker.local_fixture import GOOD_SQL, load_all, local_task

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("runs/local_integration"))
    parser.add_argument("--field-feedback", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        task = local_task(Path(temporary) / "task")
        reports = {}
        for label, sql in (
            ("correct", GOOD_SQL),
            ("wrong", GOOD_SQL.replace("COALESCE(SUM(o.amount), 0)", "0")),
        ):
            episode = Episode(task, "duckdb", args.output / label, field_feedback=args.field_feedback)
            try:
                load_all(episode)
                bad = episode.act("transform", {"sql": sql.replace("o.amount", "o.missing")})
                if label == "correct":
                    assert not bad["ok"]
                assert episode.act("transform", {"sql": sql})["ok"]
                episode.act("submit_pipeline", {})
                reports[label] = episode.grade
            finally:
                episode.close()
        assert reports["correct"]["reward"] == 1 and reports["wrong"]["reward"] == 0.75
        # Persist the fixture so the child process reconstructs it without shared objects.
        import shutil

        fixture = args.output / "task"
        shutil.copytree(task.public.parent, fixture, dirs_exist_ok=True)
        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "elt_tinker.replay",
                "--public",
                str(fixture / "public"),
                "--hidden",
                str(fixture / "hidden"),
                "--pipeline",
                str(args.output / "correct/pipeline.json"),
                "--output",
                str(args.output / "independent_replay"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        write_json(
            args.output / "acceptance.json",
            {
                "credential_free_integration": "passed",
                "field_feedback": args.field_feedback,
                "official_rollout": "not_run",
                "tinker_optimization": "not_run",
                "independent_replay": child.stdout.strip(),
                "reports": reports,
            },
        )
        print("PASS: actual extraction, load, SQL feedback, fresh replay and rewards 1.0 / 0.75")


if __name__ == "__main__":
    main()
