#!/usr/bin/env bash
# Start the databases a suite needs and wait until they answer. Run as a step
# instead of a `services:` block because a job-level service starts even when
# every step of the job is skipped, which costs a container pull and a health
# wait on a change that touches no server file.
#
#   bash .github/scripts/ci/start-services.sh postgres [valkey]
set -euo pipefail

POSTGRES_IMAGE="postgres:18-alpine"
VALKEY_IMAGE="valkey/valkey:8-alpine"

wait_for() { # name, command...
    local name="$1"
    shift
    for _ in $(seq 1 30); do
        if docker exec "$name" "$@" > /dev/null 2>&1; then
            echo "$name is ready"
            return 0
        fi
        sleep 2
    done
    echo "::error::$name did not become ready" >&2
    docker logs "$name" >&2 || true
    return 1
}

for service in "$@"; do
    case "$service" in
        postgres)
            docker run -d --name helmly-ci-postgres -p 5432:5432 \
                -e POSTGRES_USER=helmly_ci -e POSTGRES_PASSWORD=helmly_ci -e POSTGRES_DB=helmly_ci \
                "$POSTGRES_IMAGE" > /dev/null
            wait_for helmly-ci-postgres pg_isready -U helmly_ci -d helmly_ci
            ;;
        valkey)
            docker run -d --name helmly-ci-valkey -p 6379:6379 "$VALKEY_IMAGE" > /dev/null
            wait_for helmly-ci-valkey valkey-cli ping
            ;;
        *)
            echo "unknown service: $service" >&2
            exit 2
            ;;
    esac
done
