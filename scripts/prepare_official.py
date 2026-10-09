"""Prepare a fixed-version shipping task, public inputs separated from hidden references.

Source services must be provisioned separately from the original source archives.
This command uses public downloads only, never starts cloud services.
"""

import argparse
import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
REVISION = "52a48541547a4340e19e54386350740acca80146"
ELT_COMMIT = "fcf3129df49ce5ce63ff46b2bbbbba1eaf9ec055"
COOKBOOK_COMMIT = "1c03a20fdda98156ccbec15728c0e2764b5a0bc3"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="shipping")
    parser.add_argument("--destination", choices=["snowflake"], default="snowflake")
    args = parser.parse_args()
    if args.task != "shipping":
        parser.error("Start with shipping; inspect/reconcile other tasks before expanding")
    upstream = ROOT / "materials/original/upstream/ELT-Bench"
    if not upstream.exists():
        upstream = ROOT / "data/upstream/ELT-Bench"
        if not upstream.exists():
            response = requests.get(
                f"https://codeload.github.com/uiuc-kang-lab/ELT-Bench/zip/{ELT_COMMIT}", timeout=60
            )
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                for name in archive.namelist():
                    relative = Path(*Path(name).parts[1:])
                    if not relative.parts or name.endswith("/"):
                        continue
                    if relative.is_absolute() or ".." in relative.parts:
                        raise ValueError("Unsafe upstream ZIP path")
                    path = upstream / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(name))
    task_dir = upstream / f"elt-bench/{args.destination}/{args.task}"
    public = ROOT / f"data/official/{args.task}/public"
    hidden = ROOT / f"data/official/{args.task}/hidden"
    public.mkdir(parents=True, exist_ok=True)
    hidden.mkdir(parents=True, exist_ok=True)
    config = yaml.safe_load((task_dir / "config.yaml").read_text(encoding="utf-8"))
    models = yaml.safe_load((task_dir / "data_model.yaml").read_text(encoding="utf-8"))["models"]
    counts = json.loads((upstream / "evaluation/table.json").read_text())[args.task]
    keys = json.loads((upstream / "evaluation/sort_key.json").read_text())[args.task]
    sources = {}
    for kind in ("postgres", "mongodb", "custom_api"):
        for table in config[kind]["config"]["tables"]:
            if kind == "custom_api":
                source = {"kind": "api", "path": f"/{args.task}/{table}"}
            else:
                source = {"kind": kind, "database": args.task}
            sources[table] = source
    for source in config["aws_s3"]["data"]:
        sources[source["table"]] = {"kind": "s3", "url": source["path"]}
    for source in config["flat_files"]:
        sources[source["table"]] = {
            "kind": "http",
            "url": source["path"],
            "format": source["format"],
        }
    for table, source in sources.items():
        schema_file = upstream / f"elt-bench/schemas/{args.task}/{table}.csv"
        with schema_file.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        source["columns"] = [row["column_name"].strip().lower() for row in rows]
        source["column_descriptions"] = {
            row["column_name"].strip().lower(): row["column_description"] for row in rows
        }

    audit = {"dataset_revision": REVISION, "files": [], "input_equivalence": {}}
    # Verify HF task descriptions/schema match this code snapshot. Config connection
    # values can vary; compare only the requested models and shared source schemas.
    paths = [f"inputs_{args.destination}/{args.task}/data_model.yaml"]
    paths += [f"inputs_{args.destination}/{args.task}/schemas/{table}.csv" for table in sources]
    paths += [f"gt_{args.destination}/{args.task}/{model['name']}.csv" for model in models]
    for remote in paths:
        url = f"https://huggingface.co/datasets/tttjjj/elt_bench/resolve/{REVISION}/{remote}"
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        payload = response.content
        if len(payload) > 16 * 1024 * 1024:
            raise ValueError("Unexpectedly large task/reference file")
        is_gold = remote.startswith("gt_")
        local = (hidden if is_gold else ROOT / "data/official/shipping/version_audit") / remote
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(payload)
        audit["files"].append(
            {
                "remote": remote,
                "path": str(local.relative_to(ROOT)),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
        if not is_gold:
            original = (
                task_dir / "data_model.yaml"
                if remote.endswith("data_model.yaml")
                else upstream / f"elt-bench/schemas/{args.task}/{Path(remote).name}"
            )
            if remote.endswith(".yaml"):
                equal = yaml.safe_load(payload) == yaml.safe_load(
                    original.read_text(encoding="utf-8")
                )
            else:
                equal = (
                    payload.decode().replace("\r\n", "\n").strip()
                    == original.read_text(encoding="utf-8").strip()
                )
            audit["input_equivalence"][remote] = equal
            if not equal:
                raise ValueError(f"Version mismatch between dataset and code task input: {remote}")
    reference = {"source_counts": counts, "tables": {}}
    for model in models:
        table = model["name"]
        csv_path = f"gt_{args.destination}/{args.task}/{table}.csv"
        reference["tables"][table] = {
            "csv": csv_path,
            "keys": keys.get(table, []),
            "sha256": hashlib.sha256((hidden / csv_path).read_bytes()).hexdigest(),
        }
        query = (upstream / f"evaluation/sql/{args.task}/{table}.sql").read_bytes()
        (hidden / f"{table}.sql").write_bytes(query)
        reference["tables"][table].update(
            query=f"{table}.sql", query_sha256=hashlib.sha256(query).hexdigest()
        )
    spec = {
        "id": args.task,
        "official": True,
        "sources": sources,
        "models": models,
        "provenance": {
            "elt_commit": ELT_COMMIT,
            "elt_branch": "elt_bench++",
            "cookbook_commit": COOKBOOK_COMMIT,
            "dataset_revision": REVISION,
            "ground_truth": f"gt_{args.destination}",
            "comparator": "eva_stage2.check_corretness",
        },
    }
    for path, obj in (
        (public / "task.json", spec),
        (hidden / "reference.json", reference),
        (ROOT / "runs/version_audit.json", audit),
    ):
        path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {"public": str(public), "hidden": str(hidden), "audit": audit["input_equivalence"]}
        )
    )


if __name__ == "__main__":
    main()
