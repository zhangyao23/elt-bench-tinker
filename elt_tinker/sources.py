"""Native, bounded source readers. Models select source names, never credentials/URLs."""

import io
import json
import os
import time
from urllib.parse import urlsplit

import pandas as pd
import requests

from elt_tinker.errors import InfrastructureError
from elt_tinker.task import Task, fingerprint, identifier, normalize_source, safe_child

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_ROWS = 1_000_000


def read_file_bytes(url: str) -> bytes:
    with requests.get(url, timeout=30, stream=True) as response:
        response.raise_for_status()
        output = bytearray()
        for chunk in response.iter_content(65536):
            output.extend(chunk)
            if len(output) > MAX_SOURCE_BYTES:
                raise ValueError("Source exceeds 64 MiB limit")
        return bytes(output)


def parse_bytes(payload: bytes, format: str) -> pd.DataFrame:
    stream = io.BytesIO(payload)
    if format == "csv":
        return pd.read_csv(stream, low_memory=False)
    if format == "jsonl":
        return pd.read_json(stream, lines=True)
    if format == "parquet":
        return pd.read_parquet(stream)
    if format == "json":
        return pd.DataFrame(json.loads(payload))
    raise ValueError("Unsupported file format")


class SourceReader:
    def __init__(self, task: Task, operator_config: dict | None = None):
        self.task = task
        self.config = operator_config or {}
        self.cache: dict[str, pd.DataFrame] = {}
        self.fingerprints: dict[str, str] = {}
        self.timings: list[dict] = []

    def extract(self, table: str) -> pd.DataFrame:
        if table not in self.cache:
            started = time.monotonic()
            source = self.task.spec["sources"][table]
            try:
                frame = self._read(table, source)
                frame = normalize_source(frame, source)
                columns = source["columns"]
                if not set(columns).issubset(frame.columns):
                    raise ValueError(f"Source {table} lacks required schema columns")
                if len(frame) > MAX_SOURCE_ROWS:
                    raise ValueError("Source exceeds row limit")
                frame = frame[columns].copy()
                expected = self.task.reference.get("source_fingerprints", {}).get(table)
                content_digest = fingerprint(frame)
                if expected and content_digest != expected:
                    raise ValueError("Source content differs from pinned official snapshot")
            except Exception as error:
                raise InfrastructureError(
                    f"Source extraction failed ({table}, {type(error).__name__})"
                ) from error
            self.cache[table] = frame[columns].copy()
            self.fingerprints[table] = content_digest
            self.timings.append({"table": table, "seconds": time.monotonic() - started})
        return self.cache[table].copy()

    def _read(self, table: str, source: dict) -> pd.DataFrame:
        kind = source["kind"]
        if kind == "file":
            path = safe_child(self.task.public, source["path"])
            if path.stat().st_size > MAX_SOURCE_BYTES:
                raise ValueError("Source exceeds byte limit")
            return parse_bytes(path.read_bytes(), source["format"])
        if kind == "http":
            pinned = self.config.get("http_files", {}).get(table)
            if pinned:
                from pathlib import Path

                return parse_bytes(Path(pinned).read_bytes(), source["format"])
            return parse_bytes(read_file_bytes(source["url"]), source["format"])
        if kind == "api":
            base = self.config.get("api_url", "http://localhost:5005")
            return parse_bytes(read_file_bytes(base.rstrip("/") + source["path"]), "json")
        if kind == "postgres":
            import psycopg
            from psycopg import sql

            config = dict(self.config.get("postgres", {}))
            config.update(dbname=source["database"], connect_timeout=10)
            if "password_env" in config:
                config["password"] = os.environ[config.pop("password_env")]
            with psycopg.connect(**config) as connection, connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '30000'")
                cursor.execute(
                    sql.SQL("SELECT {} FROM {}.{}").format(
                        sql.SQL(", ").join(map(sql.Identifier, source["columns"])),
                        sql.Identifier(source.get("schema", "public")),
                        sql.Identifier(table),
                    )
                )
                rows = cursor.fetchmany(MAX_SOURCE_ROWS + 1)
                return pd.DataFrame(rows, columns=source["columns"])
        if kind == "mongodb":
            from pymongo import MongoClient

            uri = os.environ[self.config["mongodb_uri_env"]]
            with MongoClient(uri, serverSelectionTimeoutMS=10000) as client:
                cursor = (
                    client[source["database"]][table]
                    .find(
                        {},
                        {"_id": 0, **dict.fromkeys(source["columns"], 1)},
                    )
                    .limit(MAX_SOURCE_ROWS + 1)
                    .max_time_ms(30000)
                )
                return pd.DataFrame(list(cursor)).reindex(columns=source["columns"])
        if kind == "s3":
            import boto3

            uri = urlsplit(source["url"])
            config = self.config.get("s3", {})
            client = boto3.client("s3", **config)
            body = client.get_object(Bucket=uri.netloc, Key=uri.path.lstrip("/"))["Body"]
            try:
                payload = body.read(MAX_SOURCE_BYTES + 1)
            finally:
                body.close()
                client.close()
            if len(payload) > MAX_SOURCE_BYTES:
                raise ValueError("Source exceeds 64 MiB limit")
            return parse_bytes(payload, "csv")
        raise ValueError(f"Unsupported source kind: {identifier(kind)}")
