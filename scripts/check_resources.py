"""Check preparation without sampling, training, or resuming a cloud warehouse."""

import argparse
import asyncio
import importlib.metadata
import json
import os
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def check_tinker(model):
    import tinker

    service = tinker.ServiceClient()
    capabilities = await service.get_server_capabilities_async()
    match = next((item for item in capabilities.supported_models if item.model_name == model), None)
    return {
        "authentication": "ok",
        "model": model,
        "available": match is not None,
        "trainable": match.trainable if match else False,
        "max_context_length": match.max_context_length if match else None,
    }


def check_snowflake():
    import snowflake.connector

    config = {key: os.environ[f"SNOWFLAKE_{key.upper()}"] for key in ("account", "user", "role")}
    config["private_key_file"] = os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"]
    with snowflake.connector.connect(**config) as connection, connection.cursor() as cursor:
        # SHOW metadata only: no warehouse parameter, SELECT, DML, or RESUME.
        cursor.execute("SHOW DATABASES LIKE 'ELT_SANDBOX'")
        database = bool(cursor.fetchall())
        cursor.execute("SHOW WAREHOUSES LIKE 'ELT_TEST_WH'")
        columns = [description[0] for description in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        warehouses = [
            {
                "name": row.get("name"),
                "state": row.get("state"),
                "auto_suspend": row.get("auto_suspend"),
            }
            for row in rows
        ]
        return {
            "authentication": "ok",
            "database_visible": database,
            "warehouses": warehouses,
            "scope": "SHOW metadata only; warehouse not resumed",
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cloud",
        action="store_true",
        help="Read-only authentication/capabilities/SHOW checks after locally loading credentials",
    )
    parser.add_argument("--model", default="Qwen/Qwen3.5-4B")
    args = parser.parse_args()
    report = {
        "credentials_present": {
            key: bool(os.getenv(key))
            for key in (
                "TINKER_API_KEY",
                "SNOWFLAKE_ACCOUNT",
                "SNOWFLAKE_USER",
                "SNOWFLAKE_PRIVATE_KEY_FILE",
            )
        },
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("tinker", "snowflake-connector-python", "psycopg", "pymongo", "boto3")
        },
        "local_ports": {},
    }
    for label, port in {"postgres": 58433, "mongodb": 58717, "s3": 58466, "api": 58505}.items():
        with socket.socket() as client:
            client.settimeout(1)
            report["local_ports"][label] = client.connect_ex(("127.0.0.1", port)) == 0
    if args.cloud:
        for name, callback in (
            ("tinker", lambda: asyncio.run(check_tinker(args.model))),
            ("snowflake", check_snowflake),
        ):
            try:
                report[name] = callback()
            except Exception as error:
                # Error messages can contain account/user/credentials: keep only the type.
                report[name] = {"authentication": "failed", "error_type": type(error).__name__}
    path = ROOT / "runs/preparation_resources.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
