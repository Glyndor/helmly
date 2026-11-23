#!/usr/bin/env python3
"""Sign a file with an Ed25519 private key (raw 32-byte seed, base64-encoded).

Usage:
  GLYNDOR_RELEASE_ED25519_KEY=<base64-private-key> sign.py <input-file> [<output-sig-file>]

The key is read from the environment and never from an argument, which would show in the
process list. If output-sig-file is omitted, writes to <input-file>.sig.
The key must be the raw 32-byte Ed25519 seed in standard base64.
"""
import base64
import os
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    key_b64 = os.environ.get("GLYNDOR_RELEASE_ED25519_KEY", "")
    if len(sys.argv) < 2 or len(sys.argv) > 3 or not key_b64:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    sig_file = sys.argv[2] if len(sys.argv) > 2 else input_file + ".sig"

    key_bytes = base64.b64decode(key_b64 + "==")
    private_key = Ed25519PrivateKey.from_private_bytes(key_bytes)

    with open(input_file, "rb") as f:
        data = f.read()

    sig = private_key.sign(data)

    with open(sig_file, "wb") as f:
        f.write(sig)

    print(f"signed {input_file} ({len(data):,} bytes) → {sig_file} ({len(sig)} bytes)")


if __name__ == "__main__":
    main()
