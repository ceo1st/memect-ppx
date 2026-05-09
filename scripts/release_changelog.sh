#!/usr/bin/env bash
# =============================================================================
# release_changelog.sh — generate/update CHANGELOG.md via git-cliff
#
# Dependency: git-cliff (https://git-cliff.org)
#   macOS:  brew install git-cliff
#   Cargo:  cargo install git-cliff
#
# Usage:
#   scripts/release_changelog.sh                Generate or refresh full CHANGELOG.md
#   scripts/release_changelog.sh --unreleased   Print the Unreleased section only (no write)
#   scripts/release_changelog.sh --tag X.Y.Z    Treat the next version as X.Y.Z when rendering
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if ! command -v git-cliff >/dev/null 2>&1; then
    echo "git-cliff not found. Install with:" >&2
    echo "  brew install git-cliff" >&2
    echo "  or: cargo install git-cliff" >&2
    exit 1
fi

MODE="full"
EXTRA_ARGS=()


while [[ $# -gt 0 ]]; do
    case "$1" in
        --unreleased)
            MODE="unreleased"
            shift
            ;;
        --tag)
            EXTRA_ARGS+=("--tag" "memect-ppx-${2:?missing version}-released")
            shift 2
            ;;
        -h|--help)
            sed -n '3,14p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

case "${MODE}" in
    full)
        git-cliff --config cliff.toml --output CHANGELOG.md "${EXTRA_ARGS[@]}"
        echo "CHANGELOG.md updated."
        ;;
    unreleased)
        git-cliff --config cliff.toml --unreleased "${EXTRA_ARGS[@]}"
        ;;
esac
