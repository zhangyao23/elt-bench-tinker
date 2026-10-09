"""Download only shipping members from the official 4.27 GB source archive using HTTP Range."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
URL = "https://drive.usercontent.google.com/download?id=11vQqNEWXoPG6sjKytAn7TtFLDMiQa17I&export=download&confirm=t"


class RangeFile(io.RawIOBase):
    def __init__(self, url):
        self.url, self.position, self.downloaded = url, 0, 0
        self.session = requests.Session()
        response = self.session.get(url, headers={"Range": "bytes=-64"}, timeout=30)
        response.raise_for_status()
        if response.status_code != 206:
            raise RuntimeError("Range unsupported; refusing a multi-GB full download")
        self.size = int(response.headers["Content-Range"].split("/")[-1])
        self.cache_start, self.cache = -1, b""

    def seek(self, offset, whence=0):
        self.position = (
            offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        )
        return self.position

    def tell(self):
        return self.position

    def read(self, size=-1):
        if size < 0:
            size = self.size - self.position
        if size > 16 * 1024 * 1024 or self.downloaded > 32 * 1024 * 1024:
            raise RuntimeError("Selective download budget exceeded")
        size = min(size, self.size - self.position)
        if size <= 0:
            return b""
        if (
            not self.cache_start
            <= self.position
            <= self.position + size
            <= self.cache_start + len(self.cache)
        ):
            end = min(self.size - 1, self.position + max(size, 262144) - 1)
            response = self.session.get(
                self.url, headers={"Range": f"bytes={self.position}-{end}"}, timeout=60
            )
            response.raise_for_status()
            if response.status_code != 206:
                raise RuntimeError("Server stopped honoring Range")
            self.cache_start, self.cache = self.position, response.content
            self.downloaded += len(self.cache)
        start = self.position - self.cache_start
        self.position += size
        return self.cache[start : start + size]

    def close(self):
        self.session.close()
        super().close()


def main():
    destination = ROOT / "data/official/shipping/source_snapshot"
    destination.mkdir(parents=True, exist_ok=True)
    audit = {"url": URL, "files": [], "method": "zipfile + HTTP Range"}
    with RangeFile(URL) as remote, zipfile.ZipFile(remote) as archive:
        names = [
            name for name in archive.namelist() if "/shipping/" in name and not name.endswith("/")
        ]
        for name in names:
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe archive path")
            info = archive.getinfo(name)
            if info.file_size > 20 * 1024 * 1024:
                raise ValueError("Unexpectedly large shipping member")
            payload = archive.read(name)
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            audit["files"].append(
                {
                    "member": name,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        audit.update(archive_bytes=remote.size, transfer_bytes=remote.downloaded)
    api_url = "https://drive.usercontent.google.com/download?id=1-Gv5g_Yg_YrR-NxH4s2tSEK3VhJQc2Q0&export=download&confirm=t"
    # Also selectively read the API archive, rather than require a full 123 MB download.
    with RangeFile(api_url) as api_remote, zipfile.ZipFile(api_remote) as archive:
        payload = archive.read("data/shipping/truck.csv")
        (destination / "truck.csv").write_bytes(payload)
        audit["files"].append(
            {
                "member": "data/shipping/truck.csv",
                "archive": "data/data_db.zip",
                "archive_url": api_url,
                "transfer_bytes": api_remote.downloaded,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    driver_url = "https://drive.usercontent.google.com/download?id=1XK_eYSqVlQiiY9GjainO2Lg6sm0QCWMa&export=download&confirm=t"
    response = requests.get(driver_url, timeout=30)
    response.raise_for_status()
    if not response.content.startswith(b"PAR1"):
        raise ValueError("Driver download is not Parquet")
    (destination / "driver.parquet").write_bytes(response.content)
    audit["files"].append(
        {
            "url": driver_url,
            "bytes": len(response.content),
            "sha256": hashlib.sha256(response.content).hexdigest(),
        }
    )
    (ROOT / "runs/source_download_audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
