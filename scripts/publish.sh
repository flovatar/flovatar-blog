#!/usr/bin/env bash
# Publish dist/ to an S3-compatible bucket (the site behind https://blog.flovatar.com).
# Usage: scripts/publish.sh [--dry-run]      (run `.venv/bin/python tools/build.py` first)
# Config: environment variables, or a gitignored scripts/publish.local.env that sets them:
#   BUCKET        target bucket name (required)
#   S3_ENDPOINT   S3 API endpoint URL (required for non-AWS storage such as Cloudflare R2)
#   AWS_PROFILE   aws CLI profile with write access to the bucket (optional)
# Each file type is uploaded with an explicit Content-Type and Cache-Control. Buckets have no directory index, so
# every page <path>/index.html is also uploaded at the extension-less key <path> (the original Medium URLs have no
# trailing slash), and the RSS feed at both /feed and /feed.xml. A final pass deletes removed files.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f scripts/publish.local.env ]] && source scripts/publish.local.env
: "${BUCKET:?set BUCKET (or create scripts/publish.local.env)}"
DRY=()
[[ "${1:-}" == "--dry-run" ]] && DRY=(--dryrun)
[[ -f dist/index.html ]] || { echo "dist/ missing: run .venv/bin/python tools/build.py" >&2; exit 1; }
ls dist/fonts/*.woff2 >/dev/null 2>&1 || echo "WARNING: dist/fonts empty (dist-private/fonts missing?)" >&2

AWS=(aws)
[[ -n "${AWS_PROFILE:-}" ]] && AWS+=(--profile "$AWS_PROFILE")
[[ -n "${S3_ENDPOINT:-}" ]] && AWS+=(--endpoint-url "$S3_ENDPOINT")
S3=("${AWS[@]}" s3)
SHORT="public, max-age=300"
DAY="public, max-age=86400"
IMMUTABLE="public, max-age=31536000, immutable"
HTML="text/html; charset=utf-8"
RSS="application/rss+xml; charset=utf-8"

# sync_type <cache-control> <content-type> <include patterns...>
sync_type() {
  local cc="$1" ct="$2"; shift 2
  local inc=()
  for p in "$@"; do inc+=(--include "$p"); done
  "${S3[@]}" sync dist/ "s3://$BUCKET/" ${DRY[@]+"${DRY[@]}"} --only-show-errors --exclude "*" "${inc[@]}" \
    --content-type "$ct" --cache-control "$cc"
}

# content-hashed CSS/JS/fonts under static/ → 1 year immutable
"${S3[@]}" sync dist/static/ "s3://$BUCKET/static/" ${DRY[@]+"${DRY[@]}"} --only-show-errors --exclude "*" --include "*.css" \
  --content-type "text/css; charset=utf-8" --cache-control "$IMMUTABLE"
"${S3[@]}" sync dist/static/ "s3://$BUCKET/static/" ${DRY[@]+"${DRY[@]}"} --only-show-errors --exclude "*" --include "*.js" \
  --content-type "text/javascript; charset=utf-8" --cache-control "$IMMUTABLE"
"${S3[@]}" sync dist/static/ "s3://$BUCKET/static/" ${DRY[@]+"${DRY[@]}"} --only-show-errors --exclude "*" --include "*.woff2" \
  --content-type "font/woff2" --cache-control "$IMMUTABLE"

# media (stable names) → 1 day
sync_type "$DAY" "image/svg+xml" "*.svg"
sync_type "$DAY" "image/png" "*.png"
sync_type "$DAY" "image/jpeg" "*.jpg" "*.jpeg"
sync_type "$DAY" "image/webp" "*.webp"
sync_type "$DAY" "image/gif" "*.gif"
sync_type "$DAY" "font/woff2" "fonts/*.woff2"

# documents → 5 minutes
sync_type "$SHORT" "$HTML" "*.html"
sync_type "$SHORT" "text/plain; charset=utf-8" "*.txt"
sync_type "$SHORT" "$RSS" "feed.xml"
"${S3[@]}" sync dist/ "s3://$BUCKET/" ${DRY[@]+"${DRY[@]}"} --only-show-errors --exclude "*" --include "*.xml" --exclude "feed.xml" \
  --content-type "application/xml; charset=utf-8" --cache-control "$SHORT"

# extension-less aliases: /feed and every page directory (<path> = <path>/index.html), plus a percent-encoded copy of
# non-ASCII keys. Uploaded every run (cheap) and kept out of the delete pass below.
ALIASES=$(mktemp)
trap 'rm -f "$ALIASES"' EXIT
printf 'feed.xml\tfeed\t%s\n' "$RSS" > "$ALIASES"
(cd dist && find . -mindepth 2 -name index.html ! -path './static/*' | sed 's|^\./||; s|/index\.html$||' | sort) |
while IFS= read -r p; do
  printf '%s/index.html\t%s\t%s\n' "$p" "$p" "$HTML"
  enc=$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1], safe="/:"))' "$p")
  if [[ "$enc" != "$p" ]]; then printf '%s/index.html\t%s\t%s\n' "$p" "$enc" "$HTML"; fi
done >> "$ALIASES"
EXCL=()
while IFS=$'\t' read -r src key ct; do
  EXCL+=(--exclude "$key")
  if [[ ${#DRY[@]} -gt 0 ]]; then echo "(dryrun) alias dist/$src → s3://$BUCKET/$key"; continue; fi
  "${S3[@]}" cp "dist/$src" "s3://$BUCKET/$key" --only-show-errors --content-type "$ct" --cache-control "$SHORT"
done < "$ALIASES"
echo "aliases: $(wc -l < "$ALIASES" | tr -d ' ')"

# final pass: delete objects that no longer exist locally (already-uploaded files are skipped)
"${S3[@]}" sync dist/ "s3://$BUCKET/" ${DRY[@]+"${DRY[@]}"} --only-show-errors --delete --exclude "static/*" --exclude "_share/*" ${EXCL[@]+"${EXCL[@]}"}
"${S3[@]}" sync dist/static/ "s3://$BUCKET/static/" ${DRY[@]+"${DRY[@]}"} --only-show-errors --delete
echo "published dist/ → s3://$BUCKET/"
