#!/usr/bin/env bash
# Canonical deploy for the MBAL Mission Control site — publishes AND verifies that GitHub Pages
# actually served the new version, so we never again think we shipped when the live CDN is stale.
#
#   Usage:  ./publish.sh "commit message"
#
# WHY THIS EXISTS (the bug it kills):
#   * GitHub Pages DEBOUNCES rapid pushes and can SKIP deploying an intermediate commit — the live
#     site then serves an older commit even though `git` says main is up to date.
#   * The Pages Fastly CDN caches per-edge-node, so grepping the repo's index.html (or even one
#     curl) can report content that isn't what a given visitor gets.
# Result: you push, it looks done, but the live page is stale — exactly the trap we hit.
#
# HOW IT FIXES IT:
#   Stamps a unique deploy id into version.json, pushes ONCE, then polls the LIVE version.json until
#   that id appears (nudging Pages with an empty commit if it skipped the deploy). It reports success
#   ONLY when the live site verifiably serves this exact deploy. Always deploy through this script.
set -euo pipefail

MSG="${1:-Publish site}"
URL_BASE="${URL_BASE:-${PAGES_URL:-}}"
if [ -z "$URL_BASE" ]; then
  echo "[publish] Set URL_BASE or PAGES_URL to the deployed site root before publishing." >&2
  exit 2
fi
DEPLOY_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$-${RANDOM}"

printf '{\n  "deploy_id": "%s",\n  "built_utc": "%s",\n  "message": %s\n}\n' \
  "$DEPLOY_ID" "$(date -u +%FT%TZ)" "$(printf '%s' "$MSG" | python -c 'import json,sys;print(json.dumps(sys.stdin.read()))')" \
  > version.json

git add -A
git commit -m "$MSG [deploy $DEPLOY_ID]"
git push origin HEAD:main
echo "[publish] pushed. Verifying GitHub Pages serves deploy $DEPLOY_ID ..."

nudged=0
for i in $(seq 1 40); do
  live="$(curl -fsSL --max-time 15 "$URL_BASE/version.json?cb=$(date +%s%N)" 2>/dev/null || true)"
  if printf '%s' "$live" | grep -q "$DEPLOY_ID"; then
    echo "[publish] LIVE — deploy $DEPLOY_ID confirmed after ~$((i*12))s. $URL_BASE/#overview"
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
