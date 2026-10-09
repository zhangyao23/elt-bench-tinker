"""Resolve the official upstream source image tags to immutable registry digests, no image pull."""

from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    images = {}
    for repository, tag in (
        ("library/postgres", "16"),
        ("library/mongo", "8.0"),
        ("localstack/localstack", "3.8.0"),
    ):
        token = requests.get(
            "https://auth.docker.io/token",
            params={"service": "registry.docker.io", "scope": f"repository:{repository}:pull"},
            timeout=30,
        ).json()["token"]
        response = requests.head(
            f"https://registry-1.docker.io/v2/{repository}/manifests/{tag}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json",
            },
            timeout=30,
        )
        response.raise_for_status()
        images[repository] = f"{repository}@{response.headers['Docker-Content-Digest']}"
    services = {
        "postgres": {
            "image": images["library/postgres"],
            "ports": ["127.0.0.1:58433:5432"],
            "environment": {
                "POSTGRES_USER": "elt",
                "POSTGRES_PASSWORD": "local_test_only",
                "POSTGRES_DB": "shipping",
            },
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -U elt -d shipping"],
                "interval": "5s",
                "retries": 20,
            },
        },
        "mongodb": {
            "image": images["library/mongo"],
            "ports": ["127.0.0.1:58717:27017"],
            "environment": {
                "MONGO_INITDB_ROOT_USERNAME": "elt",
                "MONGO_INITDB_ROOT_PASSWORD": "local_test_only",
            },
        },
        "s3": {
            "image": images["localstack/localstack"],
            "ports": ["127.0.0.1:58466:4566"],
            "environment": {"SERVICES": "s3", "AWS_DEFAULT_REGION": "us-west-2"},
        },
    }
    # No host Docker socket, fixed container names or global database volumes.
    path = ROOT / "infra/compose.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        yaml.safe_dump({"name": "elt-tinker-shipping", "services": services}, sort_keys=False),
        encoding="utf-8",
    )
    print(images)


if __name__ == "__main__":
    main()
