#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="${ROOT_DIR}/version.txt"
PROJECT_NAME="memect-ppx"
TAG_SUFFIX="released"
REMOTE="github"
FORCE=0
PUSH=0

usage() {
    cat <<'EOF'
Usage: scripts/release_tag.sh [--push] [--force] [--remote <name>]

Generate the release tag from version.txt using:
  memect-ppx-<version>-released

Options:
  --push           Push the generated tag to the remote.
  --force          Recreate the local tag and force-push it if --push is set.
  --remote <name>  Remote to push to. Default: github
  -h, --help       Show this help message.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --push)
            PUSH=1
            shift
            ;;
        --force)
            FORCE=1
            shift
            ;;
        --remote)
            REMOTE="${2:?missing remote name}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ ! -f "${VERSION_FILE}" ]]; then
    echo "version file not found: ${VERSION_FILE}" >&2
    exit 1
fi

VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"
if [[ -z "${VERSION}" ]]; then
    echo "version.txt is empty" >&2
    exit 1
fi

if [[ ! "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z]+)*$ ]]; then
    echo "invalid version in version.txt: ${VERSION}" >&2
    exit 1
fi

TAG="${PROJECT_NAME}-${VERSION}-${TAG_SUFFIX}"

cd "${ROOT_DIR}"
git rev-parse --git-dir >/dev/null

# Auto-update CHANGELOG before tagging
if command -v git-cliff >/dev/null 2>&1; then
    git-cliff --config cliff.toml --output CHANGELOG.md
    if ! git diff --quiet CHANGELOG.md; then
        git add CHANGELOG.md
        git commit -m "docs: update CHANGELOG for ${VERSION}"
    fi
fi

if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
    if [[ "${FORCE}" -ne 1 ]]; then
        echo "tag already exists: ${TAG}" >&2
        echo "rerun with --force to retag it to the current HEAD" >&2
        exit 1
    fi
    git tag -f "${TAG}" HEAD >/dev/null
else
    git tag "${TAG}" HEAD >/dev/null
fi

echo "${TAG}"

if [[ "${PUSH}" -eq 1 ]]; then
    if [[ "${FORCE}" -eq 1 ]]; then
        git push "${REMOTE}" "refs/tags/${TAG}" --force
    else
        git push "${REMOTE}" "refs/tags/${TAG}"
    fi
fi
