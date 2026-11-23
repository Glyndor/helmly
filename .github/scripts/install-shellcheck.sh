#!/usr/bin/env bash
# Install the pinned shellcheck release and put it on PATH, after checking the
# archive against the digest below. The release publishes no checksum file, so
# the digest is the one measured on the release asset itself; raise both lines
# together and say so in the pull request.
set -euo pipefail

SHELLCHECK_VERSION="0.11.0"
SHELLCHECK_SHA256="8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198" # linux.x86_64.tar.xz

dest="${RUNNER_TEMP:-/tmp}/shellcheck-${SHELLCHECK_VERSION}"
mkdir -p "$dest"
curl -fsSL --retry 3 -o "$dest/shellcheck.tar.xz" \
    "https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.linux.x86_64.tar.xz"
echo "${SHELLCHECK_SHA256}  $dest/shellcheck.tar.xz" | sha256sum --check --strict
tar -xJf "$dest/shellcheck.tar.xz" -C "$dest" --strip-components=1
echo "$dest" >> "${GITHUB_PATH:?GITHUB_PATH is not set}"
"$dest/shellcheck" --version
