"""Run the pinned project containers through the existing WSL Podman engine."""

import argparse
import json
import subprocess
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LABEL = "elt.project=elt-tinker-shipping"


def engine(distro, *arguments, check=True):
    result = subprocess.run(
        ["wsl.exe", "-d", distro, "--", "podman", "--cgroup-manager=cgroupfs", *arguments],
        capture_output=True,
    )
    output = result.stdout.decode("utf-8", errors="replace")
    if check and result.returncode:
        raise RuntimeError(
            f"Podman {' '.join(arguments[:2])}: {result.stderr.decode('utf-8', errors='replace')[-1800:]}"
        )
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "status", "stop"])
    parser.add_argument("--distro", default="PyRep-Builder-2204")
    parser.add_argument("--only", help="Comma-separated subset: postgres,mongodb,s3")
    args = parser.parse_args()
    services = yaml.safe_load((ROOT / "infra/compose.yaml").read_text(encoding="utf-8"))["services"]
    if args.only:
        requested = set(args.only.split(","))
        if not requested.issubset(services):
            raise ValueError("Unknown source service")
        services = {name: definition for name, definition in services.items() if name in requested}
    records = []
    image_file = ROOT / "runs/source_images.json"
    image_map = json.loads(image_file.read_text(encoding="utf-8")) if image_file.exists() else {}
    for service, definition in services.items():
        name = f"elt-tinker-shipping-{service}"
        existing = engine(args.distro, "inspect", name, check=False)
        matches = json.loads(existing) if existing.strip().startswith("[") else []
        metadata = matches[0] if matches else None
        if (
            metadata
            and metadata.get("Config", {}).get("Labels", {}).get("elt.project")
            != "elt-tinker-shipping"
        ):
            raise RuntimeError(f"Container name collision: {name}")
        if args.action == "start":
            if metadata and metadata.get("State", {}).get("Status") in {"configured", "created"}:
                # These owned containers never started and contain no seeded source data.
                engine(args.distro, "rm", name)
                metadata = None
            if metadata:
                if not metadata.get("State", {}).get("Running"):
                    engine(args.distro, "start", name)
            else:
                # ponytail: WSL's legacy cgroup v1 is incomplete. Trusted local fixtures
                # use no container quotas; use cgroup v2 for production resource limits.
                command = [
                    "run",
                    "--detach",
                    "--cgroups=disabled",
                    "--log-driver=k8s-file",
                    "--name",
                    name,
                    "--label",
                    LABEL,
                ]
                for port in definition["ports"]:
                    command += ["--publish", port]
                for key, value in definition["environment"].items():
                    command += ["--env", f"{key}={value}"]
                image = definition["image"]
                command.append(
                    image_map[image]["local_id"] if image in image_map else "docker.io/" + image
                )
                engine(args.distro, *command)
            time.sleep(1)
        if args.action == "stop" and metadata:
            engine(args.distro, "stop", name)
        status = engine(args.distro, "inspect", name, check=False)
        matches = json.loads(status) if status.strip().startswith("[") else []
        if matches:
            item = matches[0]
            records.append(
                {
                    "name": name,
                    "id": item["Id"],
                    "running": item["State"]["Running"],
                    "image": item.get("ImageName"),
                    "ports": definition["ports"],
                }
            )
    report = {
        "distro": args.distro,
        "engine": "podman",
        "action": args.action,
        "containers": records,
    }
    (ROOT / "runs/source_container_status.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
