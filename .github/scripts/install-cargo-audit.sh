#!/usr/bin/env bash
# Install the pinned cargo-audit release and put it on PATH, after checking the
# archive against the digest below. A security tool is downloaded on every run
# and checked before it runs; it is not compiled from source and its binary is
# never restored from a cache. The digest is the one GitHub reports for the
# release asset (`gh api repos/rustsec/rustsec/releases/tags/cargo-audit/vX.Y.Z`);
# raise both lines together.
set -euo pipefail

CARGO_AUDIT_VERSION="0.23.0"
CARGO_AUDIT_SHA256="4f01c74ed84b2dffd0b1d37012a62cdf2a92c96e25bdf8eec7fcc0d4d3eb7811" # x86_64-unknown-linux-musl.tgz

asset="cargo-audit-x86_64-unknown-linux-musl-v${CARGO_AUDIT_VERSION}.tgz"
dest="${RUNNER_TEMP:-/tmp}/cargo-audit-${CARGO_AUDIT_VERSION}"
mkdir -p "$dest"
curl -fsSL --retry 3 -o "$dest/$asset" \
    "https://github.com/rustsec/rustsec/releases/download/cargo-audit%2Fv${CARGO_AUDIT_VERSION}/${asset}"
echo "${CARGO_AUDIT_SHA256}  $dest/$asset" | sha256sum --check --strict
tar -xzf "$dest/$asset" -C "$dest" --strip-components=1
install -d "$dest/bin"
install -m 0755 "$dest/cargo-audit" "$dest/bin/cargo-audit"
echo "$dest/bin" >> "${GITHUB_PATH:?GITHUB_PATH is not set}"
"$dest/bin/cargo-audit" --version
