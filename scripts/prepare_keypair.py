"""Create a private RSA key locally; show only the public-key registration SQL."""

import os
import re
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = ROOT / "secrets"
    directory.mkdir(exist_ok=True)
    if os.name == "nt":
        identity_bytes = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"]).replace(
            b"\x00", b""
        )
        identity = "*" + re.search(rb"S-1-5-[0-9-]+", identity_bytes).group().decode("ascii")
        subprocess.run(
            ["icacls", str(directory), "/inheritance:r", "/grant:r", f"{identity}:(OI)(CI)F"],
            check=True,
            capture_output=True,
        )
    else:
        directory.chmod(0o700)
    private = directory / "snowflake_private_key.p8"
    public = directory / "snowflake_public_key.pem"
    if private.exists():
        print("Private key already exists; preserving it.")
        key = serialization.load_pem_private_key(private.read_bytes(), password=None)
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    value = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public.write_bytes(value)
    base64 = "".join(value.decode().splitlines()[1:-1])
    script = "-- Run as account administrator in a trial/test account.\n"
    script += "CREATE USER IF NOT EXISTS ELT_SDK TYPE=SERVICE;\n"
    script += f"ALTER USER ELT_SDK SET RSA_PUBLIC_KEY='{base64}';\n"
    script += "GRANT ROLE ELT_LOADER TO USER ELT_SDK;\nGRANT ROLE ELT_AGENT TO USER ELT_SDK;\n"
    script += "ALTER USER ELT_SDK SET DEFAULT_ROLE=ELT_LOADER DEFAULT_WAREHOUSE=ELT_TEST_WH;\n"
    (directory / "snowflake_register_public_key.sql").write_text(script, encoding="utf-8")
    print(
        "Generated local key pair; private key never printed. Registration SQL: secrets/snowflake_register_public_key.sql"
    )


if __name__ == "__main__":
    main()
