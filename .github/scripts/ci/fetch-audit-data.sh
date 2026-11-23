#!/usr/bin/env bash
# Take the lockfiles of one audited branch, and nothing else, into a clean directory.
#
#   AUDITED=develop bash fetch-audit-data.sh cargo | bun
#
# The checkout of the event is the only checkout. The branch is fetched as a ref and its
# files are read with `git show`, never checked out, so nothing of it lands in the
# workspace and nothing of it runs. Each file must be a regular file in that branch's tree
# (not a link) and not empty. Only the lockfile, the manifest bun needs and, for cargo,
# the audit configuration if the branch has one are copied: no `.cargo/config.toml`,
# `rust-toolchain.toml`, `bunfig.toml` or `.npmrc` reaches the tools. The copy goes to
# $RUNNER_TEMP/clean.
set -euo pipefail

: "${AUDITED:?AUDITED is not set}"
: "${RUNNER_TEMP:?RUNNER_TEMP is not set}"

case "$AUDITED" in
    develop | main) ;;
    *)
        echo "::error::'$AUDITED' is not one of the branches this audit covers"
        exit 1
        ;;
esac

case "${1:-}" in
    cargo)
        required=("internal/Cargo.lock")
        optional=("internal/.cargo/audit.toml")
        ;;
    bun)
        required=("internal/dashboard/ui/package.json" "internal/dashboard/ui/bun.lock")
        optional=()
        ;;
    *)
        echo "usage: AUDITED=<branch> fetch-audit-data.sh cargo|bun" >&2
        exit 2
        ;;
esac

git fetch --no-tags --depth=1 origin "+refs/heads/${AUDITED}:refs/remotes/origin/${AUDITED}"

clean="$RUNNER_TEMP/clean"
rm -rf -- "$clean"
mkdir -p -- "$clean"

# take PATH DESTINATION-RELATIVE-TO-CLEAN
take() {
    local path="$1" dest="$2" mode
    mode="$(git ls-tree "origin/${AUDITED}" -- "$path" | cut -d' ' -f1)"
    case "$mode" in
        100644 | 100755) ;;
        120000)
            echo "::error::$path of $AUDITED is a link"
            return 1
            ;;
        "")
            echo "::error::$AUDITED has no $path"
            return 1
            ;;
        *)
            echo "::error::$path of $AUDITED is not a regular file (mode $mode)"
            return 1
            ;;
    esac
    mkdir -p -- "$(dirname -- "$clean/$dest")"
    git show "origin/${AUDITED}:${path}" > "$clean/$dest"
    if [ ! -s "$clean/$dest" ]; then
        echo "::error::$path of $AUDITED is empty"
        return 1
    fi
}

for path in "${required[@]}"; do
    take "$path" "$(basename -- "$path")"
done
for path in "${optional[@]}"; do
    if [ -n "$(git ls-tree "origin/${AUDITED}" -- "$path")" ]; then
        take "$path" ".cargo/$(basename -- "$path")"
    fi
done
find "$clean" -type f | sort
