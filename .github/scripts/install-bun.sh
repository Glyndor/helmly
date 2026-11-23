#!/usr/bin/env bash
# Install the pinned bun release and put it on PATH. The archive is checked
# against the digest below before anything in it runs; the installer script
# that `curl | bash` fetched was neither pinned nor checked. Raise both lines
# together, with the value from the release's SHASUMS256.txt.
set -euo pipefail

BUN_VERSION="1.4.5"
BUN_SHA256="94559b3b528995a49452f9f2404cec74f2efd7d06e0a000a8cd214b02c3666eb" # bun-linux-x64.zip

dest="${RUNNER_TEMP:-/tmp}/bun-${BUN_VERSION}"
mkdir -p "$dest"
curl -fsSL --retry 3 -o "$dest/bun.zip" \
    "https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/bun-linux-x64.zip"
echo "${BUN_SHA256}  $dest/bun.zip" | sha256sum --check --strict
unzip -q -o "$dest/bun.zip" -d "$dest"
install -d "$dest/bin"
install -m 0755 "$dest/bun-linux-x64/bun" "$dest/bin/bun"
echo "$dest/bin" >> "${GITHUB_PATH:?GITHUB_PATH is not set}"
"$dest/bin/bun" --version
