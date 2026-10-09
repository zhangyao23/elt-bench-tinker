"""Fetch pinned Docker images through Windows networking, then import into WSL Podman.

The host proxy is loopback-only. Docker archives avoid changing its LAN/firewall settings.
Every registry manifest/config/layer is verified against its SHA-256 digest.
"""

import gzip
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path("F:/elt_tinker_runtime/source_images")
ACCEPT = ",".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


def verified(payload, digest):
    if hashlib.sha256(payload).hexdigest() != digest.split(":", 1)[1]:
        raise ValueError("Registry SHA-256 mismatch")
    return payload


def file_digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def fetch_image(repository, digest, directory):
    directory.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    token = session.get(
        "https://auth.docker.io/token",
        params={"service": "registry.docker.io", "scope": f"repository:{repository}:pull"},
        timeout=30,
    ).json()["token"]
    session.headers.update(Authorization=f"Bearer {token}", Accept=ACCEPT)
    base = f"https://registry-1.docker.io/v2/{repository}"
    response = session.get(f"{base}/manifests/{digest}", timeout=30)
    response.raise_for_status()
    manifest = json.loads(verified(response.content, digest))
    if "manifests" in manifest:
        selected = next(
            item
            for item in manifest["manifests"]
            if item.get("platform", {}).get("architecture") == "amd64"
            and item["platform"].get("os") == "linux"
        )
        response = session.get(f"{base}/manifests/{selected['digest']}", timeout=30)
        response.raise_for_status()
        manifest = json.loads(verified(response.content, selected["digest"]))
    config_digest = manifest["config"]["digest"]
    response = session.get(f"{base}/blobs/{config_digest}", timeout=60)
    response.raise_for_status()
    config_name = config_digest.split(":")[1] + ".json"
    (directory / config_name).write_bytes(verified(response.content, config_digest))
    layers = []
    for index, layer in enumerate(manifest["layers"]):
        layer_id = layer["digest"].split(":")[1]
        blob = directory / (layer_id + ".blob")
        if not blob.exists() or file_digest(blob) != layer_id:
            print(
                f"{repository} layer {index + 1}/{len(manifest['layers'])}: {layer['size']} bytes",
                flush=True,
            )
            with session.get(
                f"{base}/blobs/{layer['digest']}", stream=True, timeout=60
            ) as response:
                response.raise_for_status()
                with blob.open("wb") as handle:
                    shutil.copyfileobj(response.raw, handle)
            if file_digest(blob) != layer_id:
                raise ValueError("Layer integrity check failed")
        layer_path = directory / layer_id / "layer.tar"
        layer_path.parent.mkdir(exist_ok=True)
        if not layer_path.exists():
            with blob.open("rb") as handle:
                magic = handle.read(4)
            if magic[:2] == b"\x1f\x8b":
                with gzip.open(blob, "rb") as source, layer_path.open("wb") as target:
                    shutil.copyfileobj(source, target)
            elif magic == b"\x28\xb5\x2f\xfd":
                import zstandard

                with blob.open("rb") as source, layer_path.open("wb") as target:
                    zstandard.ZstdDecompressor().copy_stream(source, target)
            else:
                shutil.copyfile(blob, layer_path)
        layers.append(f"{layer_id}/layer.tar")
    archive = directory / "image.tar"
    with tarfile.open(archive, "w") as output:
        output.add(directory / config_name, arcname=config_name)
        payload = json.dumps(
            [
                {
                    "Config": config_name,
                    "RepoTags": [f"docker.io/{repository}:elt-pinned"],
                    "Layers": layers,
                }
            ]
        ).encode()
        item = tarfile.TarInfo("manifest.json")
        item.size = len(payload)
        output.addfile(item, io.BytesIO(payload))
        for layer in layers:
            output.add(directory / layer, arcname=layer)
    session.close()
    return {
        "registry_image": f"docker.io/{repository}@{digest}",
        "local_id": config_digest,
        "archive": str(archive),
        "archive_bytes": archive.stat().st_size,
    }


def main():
    output = ROOT / "runs/source_images.json"
    records = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    services = yaml.safe_load((ROOT / "infra/compose.yaml").read_text(encoding="utf-8"))["services"]
    for name, service in services.items():
        image = service["image"]
        if image in records:
            existing = subprocess.run(
                [
                    "wsl.exe",
                    "-d",
                    "PyRep-Builder-2204",
                    "--",
                    "podman",
                    "image",
                    "exists",
                    records[image]["local_id"],
                ],
                capture_output=True,
            )
            if existing.returncode == 0:
                print(f"Already imported {name}", flush=True)
                continue
        repository, digest = image.split("@")
        record = fetch_image(repository, digest, RUNTIME / name)
        linux_path = "/mnt/f/" + Path(record["archive"]).relative_to("F:/").as_posix()
        result = subprocess.run(
            ["wsl.exe", "-d", "PyRep-Builder-2204", "--", "podman", "load", "--input", linux_path],
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError(
                "Podman image import failed: "
                + result.stderr.decode("utf-8", errors="replace")[-1500:]
            )
        records[image] = record
        (ROOT / "runs/source_images.json").write_text(
            json.dumps(records, indent=2), encoding="utf-8"
        )
        print(f"Imported {name}: {record['local_id']}", flush=True)


if __name__ == "__main__":
    main()
