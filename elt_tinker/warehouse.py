"""Two concrete destinations; SQL policy lives here, separate from episode/reward logic."""

import os
import threading
import uuid

import duckdb
import pandas as pd
import sqlglot
from sqlglot import exp

from elt_tinker.errors import InfrastructureError
from elt_tinker.task import identifier

# No user-defined functions, table functions, external I/O, stored procedures or DML.
ANONYMOUS_FUNCTIONS = {"iff", "date_part", "datediff", "dateadd", "try_to_date"}
FORBIDDEN_FUNCTIONS = {
    "read_csv",
    "read_csv_auto",
    "read_json",
    "read_parquet",
    "query",
    "query_table",
    "getvariable",
    "current_setting",
    "getenv",
    "system",
    "sleep",
    "nextval",
    "setval",
}
SAFE_FUNCTIONS = {
    "abs",
    "avg",
    "sum",
    "count",
    "min",
    "max",
    "coalesce",
    "nullif",
    "if",
    "case",
    "cast",
    "try_cast",
    "extract",
    "year",
    "month",
    "day",
    "round",
    "ceil",
    "floor",
    "row_number",
    "rank",
    "dense_rank",
    "lag",
    "lead",
    "lower",
    "upper",
    "trim",
    "concat",
    "concat_ws",
    "substring",
    "length",
    "date_trunc",
    "date_diff",
    "date_add",
    "greatest",
    "least",
    "to_date",
    "str_to_date",
    "time_to_str",
    "regexp_replace",
    "nullif",
    "power",
    "sqrt",
    "split_part",
    "strftime",
    "strptime",
    "and",
    "or",
} | ANONYMOUS_FUNCTIONS


def prepare_sql(
    sql: str,
    dialect: str,
    sources: set[str],
    models: set[str],
    raw_schema: str,
    model_schema: str,
    *,
    transform: bool,
) -> str:
    if len(sql) > 50000:
        raise ValueError("SQL exceeds length limit")
    statements = sqlglot.parse(sql, read=dialect)
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("Exactly one SQL statement is required")
    tree = statements[0]
    target = None
    if transform:
        if not isinstance(tree, exp.Create) or tree.args.get("kind") not in {"TABLE", "VIEW"}:
            raise ValueError("Transformation must CREATE [OR REPLACE] TABLE/VIEW AS SELECT")
        target = tree.this
        if not isinstance(target, exp.Table) or target.catalog or target.db:
            raise ValueError("Create an unqualified output table")
        name = identifier(target.name)
        if name not in models and not name.startswith("tmp_"):
            raise ValueError("Only requested models or tmp_ intermediate tables may be written")
        if not isinstance(tree.expression, exp.Query) or tree.args.get("properties"):
            raise ValueError("Only plain CREATE AS SELECT is supported")
        selected = [name.lower() for name in tree.expression.named_selects if name != "*"]
        if len(set(selected)) != len(selected):
            raise ValueError("Duplicate output column aliases")
        target.set("db", exp.to_identifier(model_schema))
    elif not isinstance(tree, exp.Query):
        raise ValueError("Inspection accepts SELECT queries only")
    ctes = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    for node in tree.walk():
        if isinstance(node, (exp.Command, exp.Into, exp.Insert, exp.Delete, exp.Update, exp.Dot)):
            raise ValueError("Statement has forbidden side effects")
        if isinstance(node, exp.Func):
            function_name = (
                node.name.lower() if isinstance(node, exp.Anonymous) else node.sql_name().lower()
            )
            if function_name in FORBIDDEN_FUNCTIONS or function_name not in SAFE_FUNCTIONS:
                raise ValueError("External I/O or stateful functions are forbidden")
            if isinstance(node, exp.Anonymous) and function_name not in ANONYMOUS_FUNCTIONS:
                raise ValueError(f"Unapproved function: {function_name}")
        if isinstance(node, exp.Table) and node is not target:
            if not isinstance(node.this, exp.Identifier) or node.catalog:
                raise ValueError("External/table function or cross-database access is forbidden")
            name = identifier(node.name)
            schema = node.db.lower()
            if not schema and name in ctes:
                continue
            if schema not in {"", "raw", "models"}:
                raise ValueError("Only logical raw/models schemas are accessible")
            if schema == "raw" or (not schema and name in sources):
                if name not in sources:
                    raise ValueError("Unknown raw source")
                node.set("db", exp.to_identifier(raw_schema))
            elif name in models or name.startswith("tmp_"):
                node.set("db", exp.to_identifier(model_schema))
            else:
                raise ValueError("Unknown relation")
    if not transform:
        # Wrapper limits returned rows even if the model requests a larger LIMIT.
        tree = sqlglot.parse_one(
            f"SELECT * FROM ({tree.sql(dialect=dialect)}) AS inspected LIMIT 30", read=dialect
        )
    return tree.sql(dialect=dialect)


def model_sql(warehouse, sql: str, *, query: bool = False):
    """Only known SQL/data errors are policy feedback; connection/resource errors invalidate RL."""
    try:
        if query:
            return warehouse.query(sql)
        if hasattr(warehouse, "run_agent"):
            return warehouse.run_agent(sql, query=False)
        return warehouse.execute(sql)
    except duckdb.Error as error:
        if isinstance(
            error,
            (
                duckdb.ParserException,
                duckdb.BinderException,
                duckdb.CatalogException,
                duckdb.ConversionException,
                duckdb.ConstraintException,
            ),
        ):
            raise ValueError(str(error)) from error
        raise InfrastructureError(f"Warehouse failure ({type(error).__name__})") from error
    except Exception as error:
        state = getattr(error, "sqlstate", "") or ""
        if state.startswith(("42", "22", "0A")):
            raise ValueError(str(error)) from error
        raise InfrastructureError(f"Warehouse failure ({type(error).__name__})") from error


class DuckDBWarehouse:
    dialect = "duckdb"
    raw_schema = "raw"
    model_schema = "models"

    def __init__(self):
        self.connection = duckdb.connect(
            ":memory:",
            config={
                "enable_external_access": "false",
                "memory_limit": "512MB",
                "threads": "2",
            },
        )
        self.reset()

    def reset(self):
        for schema in (self.raw_schema, self.model_schema):
            self.connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            self.connection.execute(f"CREATE SCHEMA {schema}")

    def load(self, table: str, frame: pd.DataFrame):
        self.connection.register("_input", frame)
        try:
            self.connection.execute(
                f'CREATE OR REPLACE TABLE raw."{identifier(table)}" AS SELECT * FROM _input'
            )
        finally:
            self.connection.unregister("_input")

    def query(self, sql: str) -> pd.DataFrame:
        timer = threading.Timer(30, self.connection.interrupt)
        timer.start()
        try:
            result = self.connection.execute(sql)
            columns = [str(entry[0]).lower() for entry in result.description]
            if len(set(columns)) != len(columns):
                raise ValueError("Duplicate output column names")
            return result.fetchdf()
        finally:
            timer.cancel()

    def execute(self, sql: str):
        self.query(sql)

    def fetch(self, table: str, *, raw: bool = False) -> pd.DataFrame:
        schema = self.raw_schema if raw else self.model_schema
        return self.query(f'SELECT * FROM {schema}."{identifier(table)}"')

    def close(self):
        self.connection.close()


class SnowflakeWarehouse:
    dialect = "snowflake"

    def __init__(self):
        import snowflake.connector

        self.raw_schema = "ELT_RAW_" + uuid.uuid4().hex.upper()
        self.model_schema = self.raw_schema.replace("RAW", "MODEL", 1)
        config = {
            key: os.environ[f"SNOWFLAKE_{key.upper()}"]
            for key in ("account", "user", "warehouse", "database", "role")
        }
        if os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE"):
            config["private_key_file"] = os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"]
        else:
            config["password"] = os.environ["SNOWFLAKE_PASSWORD"]
        self.database = identifier(config["database"]).upper()
        self.agent_role = identifier(os.environ["SNOWFLAKE_AGENT_ROLE"]).upper()
        if self.agent_role == identifier(config["role"]).upper():
            raise ValueError("Snowflake agent must use a separate restricted role")
        self.agent_connection = None
        self.connection = snowflake.connector.connect(
            **config,
            session_parameters={
                "STATEMENT_TIMEOUT_IN_SECONDS": 30,
                "QUERY_TAG": "elt-tinker-starter",
            },
        )
        try:
            self.agent_connection = snowflake.connector.connect(
                **{**config, "role": self.agent_role},
                session_parameters={
                    "STATEMENT_TIMEOUT_IN_SECONDS": 30,
                    "QUERY_TAG": "elt-tinker-agent",
                },
            )
            with self.agent_connection.cursor() as cursor:
                cursor.execute("USE SECONDARY ROLES NONE")
            self.reset()
        except Exception:
            # Failed initialization must close both sessions without hiding its cause.
            try:
                self.close()
            except Exception:
                pass
            raise

    def reset(self):
        for schema in (self.raw_schema, self.model_schema):
            self.execute(f"CREATE OR REPLACE SCHEMA {self.database}.{schema}")
            self.execute(
                f"GRANT USAGE ON SCHEMA {self.database}.{schema} TO ROLE {self.agent_role}"
            )
            self.execute(
                f"GRANT SELECT ON FUTURE TABLES IN SCHEMA {self.database}.{schema} TO ROLE {self.agent_role}"
            )
            self.execute(
                f"GRANT SELECT ON FUTURE VIEWS IN SCHEMA {self.database}.{schema} TO ROLE {self.agent_role}"
            )
        self.execute(
            f"GRANT CREATE TABLE, CREATE VIEW ON SCHEMA {self.database}.{self.model_schema} TO ROLE {self.agent_role}"
        )

    def run_agent(self, sql: str, *, query: bool):
        with self.agent_connection.cursor() as cursor:
            cursor.execute(sql)
            if query:
                return pd.DataFrame(
                    cursor.fetchall(), columns=[d[0].lower() for d in cursor.description]
                )

    def load(self, table: str, frame: pd.DataFrame):
        from snowflake.connector.pandas_tools import write_pandas

        data = frame.copy()
        data.columns = [str(c).upper() for c in data.columns]
        success, _, rows, _ = write_pandas(
            self.connection,
            data,
            identifier(table).upper(),
            database=self.database,
            schema=self.raw_schema,
            auto_create_table=True,
            overwrite=True,
            use_logical_type=True,
        )
        if not success or rows != len(data):
            raise RuntimeError("Snowflake load did not acknowledge all source rows")

    def query(self, sql: str) -> pd.DataFrame:
        return self.run_agent(sql, query=True)

    def execute(self, sql: str):
        with self.connection.cursor() as cursor:
            cursor.execute(sql)

    def fetch(self, table: str, *, raw: bool = False) -> pd.DataFrame:
        schema = self.raw_schema if raw else self.model_schema
        return self.query(f"SELECT * FROM {self.database}.{schema}.{identifier(table).upper()}")

    def close(self):
        failures = []
        try:
            for schema in (self.raw_schema, self.model_schema):
                try:
                    self.execute(f"DROP SCHEMA IF EXISTS {self.database}.{schema} CASCADE")
                except Exception as error:
                    failures.append((schema, type(error).__name__))
        finally:
            try:
                if self.agent_connection is not None:
                    self.agent_connection.close()
            finally:
                self.connection.close()
        if failures:
            raise InfrastructureError(f"Snowflake schema cleanup requires attention: {failures}")


def make_warehouse(destination: str):
    if destination == "duckdb":
        return DuckDBWarehouse()
    if destination == "snowflake":
        return SnowflakeWarehouse()
    raise ValueError("Supported destinations: duckdb, snowflake")
