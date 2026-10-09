"""Public task inputs and operator-only reference data are kept separate."""

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Expected a simple SQL identifier")
    return value.lower()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def fingerprint(frame: pd.DataFrame) -> str:
    """Canonical values across drivers, preserving NULLs, strings and duplicate multiplicity."""
    frame = frame.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise ValueError("Duplicate column names")
    frame = frame.reindex(sorted(frame.columns), axis=1)
    rows = Counter(
        json.dumps([canonical_value(v) for v in row], ensure_ascii=False)
        for row in frame.itertuples(index=False, name=None)
    )
    payload = json.dumps([list(frame.columns), sorted(rows.items())], ensure_ascii=False).encode()
    return hashlib.sha256(payload).hexdigest()


def canonical_value(value):
    if pd.isna(value):
        return ["null"]
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return ["date", pd.Timestamp(value).isoformat()]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        return ["number", format(number.normalize(), "f")]
    return ["string", str(value)]


def normalize_source(frame: pd.DataFrame, source: dict) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    columns = source["columns"]
    if len(set(frame.columns)) != len(frame.columns) or not set(columns).issubset(frame.columns):
        raise ValueError("Source column schema mismatch")
    frame = frame[columns]
    for column, kind in source.get("column_types", {}).items():
        if kind == "number":
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        elif kind == "date":
            frame[column] = pd.to_datetime(frame[column], errors="raise")
        else:
            frame[column] = frame[column].astype("string")
    return frame


def safe_child(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Path escapes task directory")
    return path


@dataclass(frozen=True)
class Task:
    public: Path
    hidden: Path
    spec: dict
    reference: dict

    @classmethod
    def load(cls, public: str | Path, hidden: str | Path) -> "Task":
        public, hidden = Path(public).resolve(), Path(hidden).resolve()
        spec, reference = read_json(public / "task.json"), read_json(hidden / "reference.json")
        identifier(spec["id"])
        if not spec["models"] or not spec["sources"]:
            raise ValueError("Task needs at least one source and model")
        for table, source in spec["sources"].items():
            identifier(table)
            for column in source["columns"]:
                identifier(column)
        models = {identifier(model["name"]) for model in spec["models"]}
        if len(models) != len(spec["models"]):
            raise ValueError("Duplicate model names")
        if models != set(reference["tables"]):
            raise ValueError("Task models and ground truth table inventory disagree")
        if set(spec["sources"]) != set(reference["source_counts"]):
            raise ValueError("Task sources and stage-1 inventory disagree")
        for table, entry in reference["tables"].items():
            gold = safe_child(hidden, entry["csv"])
            if hashlib.sha256(gold.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError(f"Reference integrity check failed: {table}")
            if "query" in entry:
                query = safe_child(hidden, entry["query"])
                if hashlib.sha256(query.read_bytes()).hexdigest() != entry["query_sha256"]:
                    raise ValueError("Evaluation SQL integrity check failed")
        return cls(public, hidden, spec, reference)

    def describe(self) -> dict:
        return self.spec
