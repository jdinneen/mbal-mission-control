#!/usr/bin/env bash
# Canonical deploy for the MBAL Mission Control site — publishes AND verifies that GitHub Pages
# actually served the new version, so we never again think we shipped when the live CDN is stale.
#
#   Usage:  ./publish.sh "commit message" path/to/file [path/to/another-file ...]
#   Preview: ./publish.sh --dry-run "commit message" path/to/file [...]
#
# WHY THIS EXISTS (the bug it kills):
#   * GitHub Pages DEBOUNCES rapid pushes and can SKIP deploying an intermediate commit — the live
#     site then serves an older commit even though `git` says main is up to date.
#   * The Pages Fastly CDN caches per-edge-node, so grepping the repo's index.html (or even one
#     curl) can report content that isn't what a given visitor gets.
# Result: you push, it looks done, but the live page is stale — exactly the trap we hit.
#
# HOW IT FIXES IT:
#   Requires an explicit list of release files, runs the existing site checks, and stages only those
#   files plus version.json. It then stamps a unique deploy id, pushes ONCE, and polls the LIVE
#   version.json until that id appears (nudging Pages with an empty commit if it skipped the deploy).
#   It reports success ONLY when the live site verifiably serves this exact deploy.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./publish.sh "commit message" path/to/file [path/to/another-file ...]
  ./publish.sh --dry-run "commit message" path/to/file [...]

Every changed tracked file must be named explicitly. Untracked files are ignored unless named.
version.json is created and staged automatically; do not name it.
EOF
}

die() {
  echo "[publish] $*" >&2
  exit 2
}

DRY_RUN=0
case "${1:-}" in
  --dry-run)
    DRY_RUN=1
    shift
    ;;
  -h|--help)
    usage
    exit 0
    ;;
esac

if [ "$#" -lt 2 ]; then
  usage >&2
  exit 2
fi

MSG="$1"
shift
SELECTED=("$@")
if [ -z "$MSG" ]; then
  die "The commit message must not be empty."
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BRANCH="$(git symbolic-ref --quiet --short HEAD || true)"
if [ "$BRANCH" != "main" ]; then
  die "Refusing to publish branch '${BRANCH:-detached HEAD}'; switch to main first."
fi

if ! git diff --cached --quiet --; then
  die "Refusing to publish with pre-existing staged changes. Unstage them, then name release files explicitly."
fi

if [ -n "$(git status --porcelain=v1 --untracked-files=all -- ':(literal)version.json')" ]; then
  die "version.json already has local changes. Preserve or discard them before publishing."
fi

VALIDATED=()
PATHSPECS=()
for path in "${SELECTED[@]}"; do
  case "$path" in
    ""|/*|[A-Za-z]:*|..|../*|*/../*|*/..|./*|*/./*|*//*|.git|.git/*|version.json)
      die "Use a normalized repository-relative file path, not '$path'."
      ;;
  esac
  if [[ "$path" == *\\* || "$path" == *$'\n'* || "$path" == *$'\r'* ]]; then
    die "Path '$path' contains unsupported separators or line breaks."
  fi
  for seen in "${VALIDATED[@]}"; do
    if [ "$seen" = "$path" ]; then
      die "Release path '$path' was listed more than once."
    fi
  done
  if [ -d "$path" ]; then
    die "Release path '$path' is a directory. Name each intended file explicitly."
  fi
  literal_pathspec=":(literal)$path"
  if [ ! -e "$path" ] && ! git ls-files --error-unmatch -- "$literal_pathspec" >/dev/null 2>&1; then
    die "Release path '$path' does not exist and is not a tracked deletion."
  fi
  if [ -z "$(git status --porcelain=v1 --untracked-files=all -- "$literal_pathspec")" ]; then
    die "Release path '$path' has no change to publish."
  fi
  VALIDATED+=("$path")
  PATHSPECS+=("$literal_pathspec")
done
SELECTED=("${VALIDATED[@]}")

is_selected() {
  local candidate="$1"
  local selected
  for selected in "${SELECTED[@]}"; do
    if [ "$selected" = "$candidate" ]; then
      return 0
    fi
  done
  return 1
}

assert_no_unselected_tracked_changes() {
  local tracked_change
  while IFS= read -r -d '' tracked_change; do
    if ! is_selected "$tracked_change"; then
      die "Tracked change '$tracked_change' was not selected. Publish it explicitly or preserve it before releasing."
    fi
  done < <(git diff --name-only -z --)
}

assert_no_unselected_tracked_changes

echo "[publish] Running repository privacy and integrity checks..."
node scripts/check.mjs -- "${SELECTED[@]}"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "[publish] DRY RUN passed. A real release would stage only:"
  printf '  %s\n' "${SELECTED[@]}" version.json
  echo "[publish] No file was staged, committed, pushed, or changed."
  exit 0
fi

echo "[publish] Confirming local main contains no older unpushed commits..."
git fetch --quiet origin main
LOCAL_HEAD="$(git rev-parse HEAD)"
ORIGIN_HEAD="$(git rev-parse origin/main)"
if [ "$LOCAL_HEAD" != "$ORIGIN_HEAD" ]; then
  die "Local main does not exactly match origin/main. Reconcile existing commits before publishing selected files."
fi

CANONICAL_URL_BASE="https://jdinneen.github.io/mbal-mission-control"
URL_BASE="${URL_BASE:-${PAGES_URL:-$CANONICAL_URL_BASE}}"
URL_BASE="${URL_BASE%/}"
if [ "$URL_BASE" != "$CANONICAL_URL_BASE" ]; then
  die "Verification must use the canonical site: $CANONICAL_URL_BASE"
fi
DEPLOY_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$-${RANDOM}"

printf '{\n  "deploy_id": "%s",\n  "built_utc": "%s",\n  "message": %s\n}\n' \
  "$DEPLOY_ID" "$(date -u +%FT%TZ)" "$(printf '%s' "$MSG" | python -c 'import json,sys;print(json.dumps(sys.stdin.read()))')" \
  > version.json

git add -- "${PATHSPECS[@]}" ':(literal)version.json'

while IFS= read -r -d '' staged_path; do
  if [ "$staged_path" != "version.json" ] && ! is_selected "$staged_path"; then
    die "Unexpected staged path '$staged_path'; refusing to commit."
  fi
done < <(git diff --cached --name-only -z --)

for selected in "${SELECTED[@]}"; do
  if git diff --cached --quiet -- ":(literal)$selected"; then
    die "Selected path '$selected' was not staged; refusing to commit."
  fi
done
if git diff --cached --quiet -- ':(literal)version.json'; then
  die "version.json was not staged; refusing to commit."
fi
assert_no_unselected_tracked_changes
if ! git diff --quiet -- "${PATHSPECS[@]}" ':(literal)version.json'; then
  die "A selected file changed after staging; review it and rerun the release."
fi

echo "[publish] Rechecking the exact staged release content..."
node scripts/check.mjs -- "${SELECTED[@]}"
if ! git diff --quiet -- "${PATHSPECS[@]}" ':(literal)version.json'; then
  die "A selected file changed during the post-stage check; review it and rerun the release."
fi
EXPECTED_TREE="$(git write-tree)"
PARENT_HEAD="$(git rev-parse HEAD)"

echo "[publish] Committing exactly these files:"
git diff --cached --name-only --
git commit -m "$MSG [deploy $DEPLOY_ID]"
COMMIT_TREE="$(git rev-parse 'HEAD^{tree}')"
COMMIT_PARENT="$(git rev-parse 'HEAD^')"
if [ "$COMMIT_TREE" != "$EXPECTED_TREE" ] || [ "$COMMIT_PARENT" != "$PARENT_HEAD" ]; then
  die "Commit hooks or a concurrent process changed the staged tree or parent. Nothing was pushed; inspect the local commit."
fi
git push origin HEAD:main
echo "[publish] pushed. Verifying GitHub Pages serves deploy $DEPLOY_ID ..."

verify_live_assets() {
  local selected encoded expected actual status
  for selected in "${SELECTED[@]}"; do
    encoded="$(python -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$selected")"
    if git cat-file -e "HEAD:$selected" 2>/dev/null; then
      expected="$(git cat-file blob "HEAD:$selected" | sha256sum | awk '{print $1}')"
      actual="$(curl -fsSL --max-time 20 "$URL_BASE/$encoded?cb=$(date +%s%N)" | sha256sum | awk '{print $1}')" || return 1
      if [ "$actual" != "$expected" ]; then
        return 1
      fi
    else
      status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$URL_BASE/$encoded?cb=$(date +%s%N)" || true)"
      if [ "$status" != "404" ]; then
        return 1
      fi
    fi
  done
  return 0
}

nudged=0
for i in $(seq 1 40); do
  live="$(curl -fsSL --max-time 15 "$URL_BASE/version.json?cb=$(date +%s%N)" 2>/dev/null || true)"
  if printf '%s' "$live" | grep -q "$DEPLOY_ID" && verify_live_assets; then
    echo "[publish] LIVE — receipt and every selected web asset confirmed after ~$((i*12))s. $URL_BASE/#overview"
    exit 0
  fi
  # if Pages hasn't picked it up after ~90s, nudge once (handles a skipped deploy)
  if [ "$i" -ge 8 ] && [ "$nudged" -eq 0 ]; then
    echo "[publish] not live yet — nudging Pages with an empty commit"
    git commit --allow-empty -m "nudge pages [deploy $DEPLOY_ID]"
    git push origin HEAD:main
    nudged=1
  fi
  sleep 12
done
echo "[publish] NOT confirmed live after timeout. Check the repository deployments page."
exit 1
