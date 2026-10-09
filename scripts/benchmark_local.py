"""Repeat the same real local pipeline; no model/API throughput claims."""

import argparse
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

from elt_tinker.environment import Episode
from elt_tinker.local_fixture import GOOD_SQL, load_all, local_task
from elt_tinker.task import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=10)
    args = parser.parse_args()
    timings, peaks = [], []
    with tempfile.TemporaryDirectory() as temporary:
        task = local_task(Path(temporary) / "task")
        for index in range(args.repetitions):
            tracemalloc.start()
            start = time.perf_counter()
            episode = Episode(task, "duckdb", Path(temporary) / str(index))
            try:
                load_all(episode)
                assert episode.act("transform", {"sql": GOOD_SQL})["ok"]
                episode.act("submit_pipeline", {})
                assert episode.grade["valid"] and episode.grade["reward"] == 1
                timings.append(
                    {
                        "total": time.perf_counter() - start,
                        "grade": episode.grade["seconds"],
                        "phases": episode.grade["timings"],
                    }
                )
            finally:
                episode.close()
            peaks.append(tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()
    report = {
        "scope": "small local fixture; DuckDB/CSV execution only",
        "repetitions": args.repetitions,
        "median_seconds": statistics.median(t["total"] for t in timings),
        "median_grading_seconds": statistics.median(t["grade"] for t in timings),
        "median_python_peak_bytes": statistics.median(peaks),
        "samples": timings,
        "memory_scope": "tracemalloc Python allocations; excludes native DuckDB/Arrow allocations",
    }
    write_json(args.output, report)
    print({key: value for key, value in report.items() if key != "samples"})


if __name__ == "__main__":
    main()
