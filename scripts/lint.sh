#!/usr/bin/env bash
# =============================================================================
# lint.sh
# =============================================================================
# Description: Runs shellcheck static analysis on all Helmly shell scripts.
#              Reports errors, warnings and style issues without executing them.
#
# Usage:
#   bash scripts/lint.sh
#
# Requirements:
#   - shellcheck must be installed (apt install shellcheck / brew install shellcheck)
# =============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# -----------------------------------------------------------------------------
# Check shellcheck is installed
# -----------------------------------------------------------------------------
if ! command -v shellcheck &>/dev/null; then
    echo -e "${RED}Error: shellcheck is not installed.${RESET}" >&2
    echo -e "${YELLOW}Install it with: apt install shellcheck${RESET}" >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# Collect all shell scripts: every tracked *.sh, so a new script is checked without
# anyone remembering to add it to a list
# -----------------------------------------------------------------------------
mapfile -t SCRIPTS < <(git -C "$ROOT_DIR" ls-files '*.sh' | sed "s#^#$ROOT_DIR/#")
if [[ "${#SCRIPTS[@]}" -eq 0 ]]; then
    echo -e "${RED}Error: no tracked shell script found.${RESET}" >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# Run shellcheck
# -----------------------------------------------------------------------------
echo -e "${BOLD}${CYAN}Helmly Shell Linter${RESET}\n"

ERRORS=0

for script in "${SCRIPTS[@]}"; do
    echo -e "${CYAN}Checking: ${BOLD}${script##*/}${RESET}"
    if shellcheck -x "$script"; then
        echo -e "${GREEN}✔ OK${RESET}\n"
    else
        echo -e "${RED}✖ Issues found${RESET}\n"
        ERRORS=$((ERRORS + 1))
    fi
done

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
if [[ "$ERRORS" -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}All scripts passed.${RESET}"
else
    echo -e "${RED}${BOLD}${ERRORS} script(s) have issues.${RESET}"
    exit 1
fi
