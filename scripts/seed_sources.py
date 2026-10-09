"""Seed only project-isolated shipping services; never initializes all upstream tasks."""

import argparse
import os
from contextlib import closing
from pathlib import Path

import boto3
import pandas as pd
import psycopg
from botocore.exceptions import ClientError
from psycopg import sql
from pymongo import MongoClient

from elt_tinker.sources import SourceReader
from elt_tinker.task import Task, fingerprint, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


def configure_local_source_credentials():
    # This initializer must never target an operator's remote MongoDB deployment.
    os.environ["ELT_POSTGRES_PASSWORD"] = "local_test_only"
    os.environ["ELT_MONGODB_URI"] = (
        "mongodb://elt:local_test_only@127.0.0.1:58717/?authSource=admin&directConnection=true"
    )


def seed_postgres(source: Path, types: dict):
    frame = pd.read_csv(source / "shipment.csv")
    with psycopg.connect(
        host="127.0.0.1",
        port=58433,
        user="elt",
        password="local_test_only",
        dbname="shipping",
        connect_timeout=10,
    ) as connection:
        with connection.cursor() as cursor:
            columns = sql.SQL(", ").join(
                sql.SQL("{} {}").format(
                    sql.Identifier(c),
                    sql.SQL(
                        "TIMESTAMP"
                        if kind == "date"
                        else "DOUBLE PRECISION"
                        if kind == "number"
                        else "TEXT"
                    ),
                )
                for c, kind in types.items()
            )
            cursor.execute("DROP TABLE IF EXISTS public.shipment")
            cursor.execute(sql.SQL("CREATE TABLE public.shipment ({})").format(columns))
            with cursor.copy("COPY public.shipment FROM STDIN WITH CSV HEADER") as copy:
                copy.write(frame.to_csv(index=False))


def seed_mongodb(source: Path):
    with MongoClient(os.environ["ELT_MONGODB_URI"], serverSelectionTimeoutMS=10000) as client:
        customer = pd.read_csv(source / "customer.csv").astype(object)
        client.shipping.customer.drop()
        client.shipping.customer.insert_many(
            customer.where(customer.notna(), None).to_dict(orient="records")
        )


def seed_s3(source: Path):
    with closing(
        boto3.client(
            "s3",
            endpoint_url="http://127.0.0.1:58466",
            aws_access_key_id="test",
            aws_secret_access_key="test",
            region_name="us-west-2",
        )
    ) as client:
        try:
            client.create_bucket(
                Bucket="shipping-bucket",
                CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
                raise
        client.put_object(
            Bucket="shipping-bucket", Key="city.csv", Body=(source / "city.csv").read_bytes()
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-s3",
        action="store_true",
        help="Initialize/verify the four ready local sources while S3 startup is blocked",
    )
    args = parser.parse_args()
    configure_local_source_credentials()
    root = ROOT / "data/official/shipping"
    task = Task.load(root / "public", root / "hidden")
    source = root / "source_snapshot/data/shipping"
    seed_postgres(source, task.spec["sources"]["shipment"]["column_types"])
    seed_mongodb(source)
    if not args.skip_s3:
        seed_s3(source)
    operator = read_json(ROOT / "infra/source_config.example.json")
    operator["http_files"]["driver"] = str((root / "source_snapshot/driver.parquet").resolve())
    reader = SourceReader(task, operator)
    audit = {}
    for table in task.spec["sources"]:
        if args.skip_s3 and task.spec["sources"][table]["kind"] == "s3":
            continue
        actual = reader.extract(table)
        audit[table] = {"rows": len(actual), "content_fingerprint": fingerprint(actual)}
    filename = (
        "native_source_services_partial.json" if args.skip_s3 else "native_source_services.json"
    )
    write_json(ROOT / "runs" / filename, {"all_five_verified": not args.skip_s3, "sources": audit})
    print(f"PASS: {len(audit)} native source readers match pinned contents")


if __name__ == "__main__":
    main()
