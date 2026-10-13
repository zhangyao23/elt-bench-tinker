"""Bound model observations and review public requirements; never compute rewards."""

import re

from sqlglot import exp

YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def compact_tool_feedback(feedback: dict) -> dict:
    """Copy modified containers only; preserve full Episode trace and original row values."""
    view = dict(feedback)
    if "error" in view and len(view["error"]) > 600:
        view["error"] = view["error"][:600]
        view["error_truncated"] = True
    if isinstance(view.get("result"), dict):
        result = dict(view["result"])
        rows = result.get("rows")
        if isinstance(rows, list):
            result["rows"] = rows[:6]
            result["rows_shown"] = min(len(rows), 6)
            result["rows_truncated"] = len(rows) > 6
            if len(rows) > 6:
                result["query_hint"] = "Use WHERE or aggregate queries for the specific uncertainty."
        view["result"] = result
    return view


def public_condition_warnings(model: dict, query: exp.Expression) -> list[dict]:
    """Absence of a public year literal is a review hint, not proof that SQL is wrong."""
    literals = {
        year
        for literal in query.find_all(exp.Literal)
        for year in YEAR.findall(str(literal.this))
    }
    warnings = []
    for column in model.get("columns", []):
        description = column.get("description", "")
        missing = sorted(set(YEAR.findall(description)) - literals)
        if missing:
            warnings.append(
                {
                    "column": column["name"],
                    "public_requirement": description,
                    "years_absent_as_sql_literals": missing,
                    "review_hint": "Review the requested time scope, including equivalent expressions. This is not a correctness verdict.",
                }
            )
    return warnings
