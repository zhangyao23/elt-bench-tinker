"""Pin source contents/schema and check official source counts + driver Parquet equivalence."""

import hashlib
from pathlib import Path

import pandas as pd

from elt_tinker.task import fingerprint, normalize_source, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


def main():
    root = ROOT / "data/official/shipping"
    public, hidden = root / "public/task.json", root / "hidden/reference.json"
    spec, reference = read_json(public), read_json(hidden)
    audit = {
        "source_inventory": {},
        "scope": "source bytes/schema/counts; not credentialed rollout",
    }
    snapshot = root / "source_snapshot"
    for table, source in spec["sources"].items():
        path = (
            snapshot / "driver.parquet"
            if table == "driver"
            else snapshot / "truck.csv"
            if table == "truck"
            else snapshot / f"data/shipping/{table}.csv"
        )
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        source["column_types"] = {
            str(c).lower(): "date"
            if c == "ship_date"
            else "number"
            if pd.api.types.is_numeric_dtype(frame[c])
            else "string"
            for c in frame.columns
        }
        frame = normalize_source(frame, source)
        if len(frame) != reference["source_counts"][table]:
            raise ValueError("Official row count mismatch")
        reference.setdefault("source_fingerprints", {})[table] = fingerprint(frame)
        audit["source_inventory"][table] = {
            "rows": len(frame),
            "path": str(path.relative_to(ROOT)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "content_fingerprint": fingerprint(frame),
            "column_types": source["column_types"],
        }
    original_parquet = snapshot / "data/shipping/driver.parquet"
    audit["driver_download_matches_archive"] = (
        original_parquet.read_bytes() == (snapshot / "driver.parquet").read_bytes()
    )
    if not audit["driver_download_matches_archive"]:
        raise ValueError("Flat-file endpoint differs from official archive")
    write_json(public, spec)
    write_json(hidden, reference)
    write_json(ROOT / "runs/source_integrity_audit.json", audit)
    print(
        "PASS: all 5 official source counts/schemas/content digests pinned; Parquet bytes match archive"
    )


if __name__ == "__main__":
    main()
