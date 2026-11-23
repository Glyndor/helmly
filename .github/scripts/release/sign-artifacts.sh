#!/usr/bin/env bash
# Sign the release artifacts in a directory, one detached signature each.
#
#   bash sign-artifacts.sh <artifacts-dir>
#
# The directory is data: the artifacts the build jobs uploaded, downloaded outside the
# checkout, one folder per architecture. It must hold exactly the folders x86_64 and arm64,
# and each exactly the three files of a release under their expected names, as regular files
# and not links. Anything else (an extra name, a link, a folder, a file that could be imported
# as code) fails before anything is signed. The signer is the trusted script of the checkout,
# run by `python3 -I` (SIGN_PYTHON, the interpreter of a venv with the pinned library) so that
# nothing of the data directory or the working directory is imported. The key comes from
# GLYNDOR_RELEASE_ED25519_KEY, never from an argument.
set -euo pipefail

: "${GLYNDOR_RELEASE_ED25519_KEY:?the signing key is not set}"
: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is not set}"
dir="${1:?usage: sign-artifacts.sh <artifacts-dir>}"
python="${SIGN_PYTHON:-python3}"

case "$dir" in
    /*) ;;
    *)
        echo "::error::the artifacts directory must be an absolute path"
        exit 1
        ;;
esac
if [ ! -d "$dir" ] || [ -L "$dir" ]; then
    echo "::error::$dir is not a directory"
    exit 1
fi

listing() { find "$1" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort; }

archs=(x86_64 arm64)
if [ "$(listing "$dir")" != "$(printf '%s\n' "${archs[@]}" | LC_ALL=C sort)" ]; then
    echo "::error::$dir must hold exactly: ${archs[*]}"
    exit 1
fi

files=()
for arch in "${archs[@]}"; do
    folder="$dir/$arch"
    if [ ! -d "$folder" ] || [ -L "$folder" ]; then
        echo "::error::$folder is not a directory"
        exit 1
    fi
    expected=("helmly-dashboard-backend-linux-$arch" "helmly-dashboard-frontend-assets-linux-$arch.tar.gz" "helmly-dashboard-frontend-linux-$arch")
    if [ "$(listing "$folder")" != "$(printf '%s\n' "${expected[@]}" | LC_ALL=C sort)" ]; then
        echo "::error::$folder must hold exactly: ${expected[*]}"
        listing "$folder" >&2
        exit 1
    fi
    for name in "${expected[@]}"; do
        if [ -L "$folder/$name" ] || [ ! -f "$folder/$name" ]; then
            echo "::error::$folder/$name is not a regular file"
            exit 1
        fi
        files+=("$folder/$name")
    done
done

for file in "${files[@]}"; do
    "$python" -I "$GITHUB_WORKSPACE/.github/scripts/sign.py" "$file"
done
