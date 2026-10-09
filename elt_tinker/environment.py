"""Stateful ELT tools and terminal execution grading, with invalid infrastructure excluded."""

import hashlib
import io
import json
import os
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import sqlglot
from sqlglot import exp

from elt_tinker.errors import InfrastructureError, SQLPolicyError
from elt_tinker.sources import SourceReader
from elt_tinker.task import Task, fingerprint, safe_child, write_json
from elt_tinker.upstream.eva_stage2 import check_corretness, sort_by_keys
from elt_tinker.warehouse import make_warehouse, model_sql, prepare_sql


def safe_error(error: Exception) -> str:
    message = str(error)
    for key, value in os.environ.items():
        if (
            any(marker in key.upper() for marker in ("PASSWORD", "SECRET", "TOKEN", "API_KEY"))
            and len(value) >= 4
        ):
            message = message.replace(value, "[REDACTED]")
    return message[:1500]


def csv_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(frame.to_csv(index=False)), dtype=str, keep_default_na=True)


def feedback_value(value):
    if isinstance(value, (date, datetime)):
        return None if pd.isna(value) else value.isoformat()
    return value


class Episode:
    def __init__(
        self,
        task: Task,
        destination: str,
        output: Path,
        operator_config: dict | None = None,
        max_calls: int = 48,
        *,
        field_feedback: bool = False,
    ):
        self.task, self.destination, self.output = task, destination, Path(output)
        self.operator_config = operator_config or {}
        self.sources = SourceReader(task, self.operator_config)
        self.warehouse = make_warehouse(destination)
        self.configured: dict[str, dict] = {}
        self.loaded: set[str] = set()
        self.sql: list[str] = []
        self.created_models: set[str] = set()
        self.trace: list[dict] = []
        self.timings: list[dict] = []
        self.max_calls = max_calls
        self.field_feedback = field_feedback
        self.done = self.submitted = False
        self.invalid_reason: str | None = None
        self.grade: dict | None = None

    def initial_prompt(self) -> str:
        return (
            "Solve this ELT task with tools. Inspect task and schemas, configure_load each source, "
            "then run_load. Use transform with one CREATE OR REPLACE TABLE/VIEW models.<model> AS SELECT "
            "per call; read sources as raw.<table>, outputs as models.<table>. Query actual data "
            "and repair SQL errors. Call submit_pipeline to finish; a final message alone does "
            "not submit. Scoring verifies loaded raw content, replays on an empty warehouse, "
            "then queries actual tables. Hidden reference data is unavailable. "
            "Keep explanations brief and prioritize tool execution. Query only to resolve a "
            "specific uncertainty; do not repeat successful inspections. Use exact declared "
            "source names. Work on one output model per response, and submit your best pipeline "
            "before the limits even if some transformations are uncertain. "
            f"SQL dialect: {self.warehouse.dialect}. Task ID: {self.task.spec['id']}. "
            "Call inspect_task once to read its complete specification."
        )

    def sql_for(self, sql: str, *, transform: bool) -> str:
        return prepare_sql(
            sql,
            self.warehouse.dialect,
            set(self.task.spec["sources"]),
            {m["name"] for m in self.task.spec["models"]},
            self.warehouse.raw_schema,
            self.warehouse.model_schema,
            transform=transform,
        )

    def _load(self, reader: SourceReader, table: str):
        frame = reader.extract(table)
        started = time.monotonic()
        try:
            self.warehouse.load(table, frame)
        except Exception as error:
            raise InfrastructureError(
                f"Warehouse loading failed ({type(error).__name__})"
            ) from error
        self.timings.append(
            {"phase": "load", "table": table, "seconds": time.monotonic() - started}
        )
        return frame

    def pipeline(self) -> dict:
        return {
            "version": 1,
            "task_id": self.task.spec["id"],
            "destination": self.destination,
            "task_sha256": hashlib.sha256(
                (self.task.public / "task.json").read_bytes()
            ).hexdigest(),
            "load": self.configured,
            "transform_sql": self.sql,
            "provenance": self.task.spec.get("provenance", {}),
        }

    def _output_schema_feedback(self, target: exp.Table) -> dict | None:
        model = next(
            (m for m in self.task.spec["models"] if m["name"].lower() == target.name.lower()),
            None,
        )
        if model is None:
            return None
        if not model.get("columns"):
            return {"available": False, "reason": "No public output-column specification"}
        started = time.monotonic()
        try:
            query = exp.select("*").from_(target.copy()).limit(0).sql(dialect=self.warehouse.dialect)
            frame = model_sql(self.warehouse, query, query=True)
            required = [column["name"] for column in model["columns"]]
            actual = list(frame.columns)
            required_names = {name.lower() for name in required}
            actual_names = {name.lower() for name in actual}
            missing = sorted(required_names - actual_names)
            return {
                "available": True,
                "model": model["name"],
                "required_columns": required,
                "actual_columns": actual,
                "column_types": frame.attrs.get("column_types", {}),
                "missing_columns": missing,
                "extra_columns": sorted(actual_names - required_names),
                "coverage_complete": not missing,
                "values_verified": False,
            }
        except ValueError as error:
            # SQL already executed and was recorded; a failed inspection must not undo it.
            return {"available": False, "error": safe_error(error)}
        finally:
            self.timings.append(
                {"phase": "field_feedback", "table": model["name"], "seconds": time.monotonic() - started}
            )

    def _dispatch(self, action: str, arguments: dict):
        if action == "inspect_task":
            return self.task.describe()
        if action == "configure_load":
            table, kind = arguments["table"], arguments["source_kind"]
            if (
                kind != self.task.spec["sources"][table]["kind"]
                or arguments.get("sync_mode", "full_refresh") != "full_refresh"
            ):
                raise ValueError("Use the declared source kind and full_refresh")
            self.configured[table] = {"source_kind": kind, "sync_mode": "full_refresh"}
            return {"configured": table}
        if action == "run_load":
            tables = arguments.get("tables") or sorted(self.configured)
            if not isinstance(tables, list) or not tables:
                raise ValueError("Configure at least one source before loading")
            result = {}
            for table in tables:
                if table not in self.configured:
                    raise ValueError(f"Unconfigured source: {table}")
                frame = self._load(self.sources, table)
                self.loaded.add(table)
                result[table] = {"rows": len(frame), "columns": list(frame.columns)}
            return result
        if action == "query":
            frame = model_sql(
                self.warehouse, self.sql_for(arguments["sql"], transform=False), query=True
            )
            return {
                "columns": list(frame.columns),
                "column_types": frame.attrs.get("column_types", {}),
                "rows": json.loads(frame.map(feedback_value).to_json(orient="records")),
            }
        if action == "transform":
            sql = self.sql_for(arguments["sql"], transform=True)
            target = sqlglot.parse_one(sql, read=self.warehouse.dialect).this
            model_sql(self.warehouse, sql)
            self.sql.append(arguments["sql"])
            if target.name.lower() in {model["name"].lower() for model in self.task.spec["models"]}:
                self.created_models.add(target.name.lower())
            feedback = {"executed": True, "pipeline_statements": len(self.sql)}
            if self.field_feedback:
                schema_feedback = self._output_schema_feedback(target)
                if schema_feedback is not None:
                    feedback["output_schema"] = schema_feedback
            return feedback
        if action == "submit_pipeline":
            self.submitted = self.done = True
            write_json(self.output / "pipeline.json", self.pipeline())
            self.grade = self.evaluate()
            return {"submitted": True, "valid": self.grade["valid"], "reward": self.grade["reward"]}
        raise ValueError("Unknown tool")

    def act(self, action: str, arguments: dict) -> dict:
        if self.done:
            raise ValueError("Episode already ended")
        started = time.monotonic()
        try:
            result = {"ok": True, "result": self._dispatch(action, arguments)}
        except InfrastructureError as error:
            self.invalid_reason, self.done = safe_error(error), True
            result = {"ok": False, "error": self.invalid_reason, "error_kind": "infrastructure"}
        except SQLPolicyError as error:
            result = {
                "ok": False,
                "error": safe_error(error),
                "error_kind": "action",
                "error_category": "sql_policy",
            }
        except (KeyError, ValueError, TypeError, sqlglot.errors.ParseError) as error:
            result = {"ok": False, "error": safe_error(error), "error_kind": "action"}
        except Exception as error:
            self.invalid_reason, self.done = f"Environment failure ({type(error).__name__})", True
            result = {"ok": False, "error": self.invalid_reason, "error_kind": "infrastructure"}
        calls_used = len(self.trace) + 1
        if calls_used >= self.max_calls:
            self.done = True
        state = {
            "configured_sources": sorted(self.configured),
            "loaded_sources": sorted(self.loaded),
            "successful_transform_statements": len(self.sql),
            "created_models": sorted(self.created_models),
            "tool_calls_used": calls_used,
            "tool_calls_remaining": max(0, self.max_calls - calls_used),
            "submitted": self.submitted,
            "ended": self.done,
        }
        result["state"] = state
        if len(json.dumps(result, default=str)) > 12000:
            result = {
                "ok": False,
                "error": "Feedback exceeds 12k; select fewer/smaller columns",
                "error_kind": "action",
                "state": state,
            }
        self.trace.append(
            {
                "tool": action,
                "arguments": arguments,
                "feedback": result,
                "seconds": time.monotonic() - started,
            }
        )
        write_json(self.output / "tool_trace.json", self.trace)
        return result

    def _verify_raw(self, reader: SourceReader) -> dict:
        result = {}
        for table in self.task.spec["sources"]:
            source = reader.extract(table)
            if len(source) != self.task.reference["source_counts"][table]:
                raise InfrastructureError(f"Pinned source row count mismatch: {table}")
            actual = model_sql(
                self.warehouse, f"SELECT * FROM {self.warehouse.raw_schema}.{table}", query=True
            )
            actual_digest = fingerprint(actual)
            result[table] = {
                "rows": len(actual),
                "expected_rows": len(source),
                "match": reader.fingerprints[table] == actual_digest,
                "actual_fingerprint": actual_digest,
                "source_fingerprint": reader.fingerprints[table],
            }
        if not all(entry["match"] for entry in result.values()):
            raise ValueError("Raw warehouse content mismatch")
        return result

    def _model_result(self, table: str, entry: dict):
        if "query" in entry:
            query = sqlglot.parse_one(
                safe_child(self.task.hidden, entry["query"]).read_text(encoding="utf-8"),
                read=self.warehouse.dialect,
            )
            for relation in query.find_all(exp.Table):
                if (
                    relation.catalog
                    or relation.db.lower() != self.task.spec["id"]
                    or relation.name.lower() != table
                ):
                    raise InfrastructureError("Unexpected relation in pinned evaluation SQL")
                relation.set("db", exp.to_identifier(self.warehouse.model_schema))
            sql = query.sql(dialect=self.warehouse.dialect)
        else:
            sql = f"SELECT * FROM {self.warehouse.model_schema}.{table}"
        actual = model_sql(self.warehouse, sql, query=True)
        if len(set(c.lower() for c in actual.columns)) != len(actual.columns):
            raise ValueError("Duplicate output column names")
        frame = csv_frame(actual)
        evidence = self.output / "warehouse_results"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / f"{table}.sql").write_text(sql, encoding="utf-8")
        frame.to_csv(evidence / f"{table}.csv", index=False)
        return frame

    def _grade_models(self, report):
        matched, total = 0, 0
        for table, entry in self.task.reference["tables"].items():
            gold = pd.read_csv(
                safe_child(self.task.hidden, entry["csv"]), dtype=str, keep_default_na=True
            )
            total += len(gold.columns)
            try:
                actual = self._model_result(table, entry)
                result = check_corretness(
                    sort_by_keys(gold, entry["keys"]), sort_by_keys(actual, entry["keys"])
                )
                result["row_count_match"] = len(gold) == len(actual)
                result["extra_columns"] = sorted(
                    {c.lower() for c in actual.columns} - {c.lower() for c in gold.columns}
                )
                result["missing_columns"] = sorted(
                    {c.lower() for c in gold.columns} - {c.lower() for c in actual.columns}
                )
                result["match"] &= result["row_count_match"]
                if result["row_count_match"]:
                    matched += len(result["matched"])
                report["models"][table] = result
            except ValueError as error:
                report["models"][table] = {"match": False, "error": safe_error(error)}
        report["reward"] = matched / total
        report["all_models_match"] = all(entry["match"] for entry in report["models"].values())

    def evaluate(self) -> dict:
        started = time.monotonic()
        report = {
            "valid": True,
            "reward": 0.0,
            "submitted": self.submitted,
            "raw": {},
            "models": {},
            "destination": self.destination,
            "task_id": self.task.spec["id"],
            "official": self.task.spec.get("official", False),
            "provenance": self.task.spec.get("provenance", {}),
        }
        try:
            if self.invalid_reason:
                raise InfrastructureError(self.invalid_reason)
            if not self.submitted:
                raise ValueError("Explicit pipeline submission required")
            if self.loaded != set(self.task.spec["sources"]):
                raise ValueError("Every required source must be loaded")
            report["interactive_raw"] = self._verify_raw(self.sources)
            try:
                self.warehouse.reset()
            except Exception as error:
                raise InfrastructureError("Warehouse reset failed") from error
            # Fresh reader, no dependency on interactive extraction cache.
            replay_sources = SourceReader(self.task, self.operator_config)
            replay_start = time.monotonic()
            for table in sorted(self.configured):
                self._load(replay_sources, table)
            for sql in self.sql:
                model_sql(self.warehouse, self.sql_for(sql, transform=True))
            report["raw"] = self._verify_raw(replay_sources)
            self.timings.append({"phase": "replay", "seconds": time.monotonic() - replay_start})
            self._grade_models(report)
            self.timings.extend({"phase": "extract", **entry} for entry in replay_sources.timings)
        except InfrastructureError as error:
            self.invalid_reason = safe_error(error)
            report.update(valid=False, reward=None, error=self.invalid_reason)
        except ValueError as error:
            report["error"] = safe_error(error)
        except Exception as error:
            self.invalid_reason = f"Grading failed ({type(error).__name__})"
            report.update(valid=False, reward=None, error=self.invalid_reason)
        report["seconds"] = time.monotonic() - started
        report["timings"] = self.timings + [
            {"phase": "extract", **entry} for entry in self.sources.timings
        ]
        write_json(self.output / "grade.json", report)
        return report

    def close(self):
        self.warehouse.close()
