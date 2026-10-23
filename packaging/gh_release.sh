#!/usr/bin/env bash
# Publish build outputs to a GitHub Release, with the retries that four parallel
# platform jobs need: they race to create the same release, and an asset name can
# already exist from a previous run of the same tag.
#
# Uses only curl and the Python interpreter that built the app - no jq, no gh.
#
#   GH_TOKEN=... ./gh_release.sh ensure                # print release id + upload base
#   GH_TOKEN=... ./gh_release.sh upload <file> [name]  # replace an asset of that name
#
# Environment: GITHUB_REPOSITORY (owner/name), GITHUB_REF_NAME (the tag),
# GITHUB_API_URL (default https://api.github.com).

set -u -o pipefail

API="${GITHUB_API_URL:-https://api.github.com}"
REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
TAG="${GITHUB_REF_NAME:?GITHUB_REF_NAME is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"

PY="${PY:-python3}"

hdr_auth() {
  printf '%s\0' -H "Authorization: Bearer $GH_TOKEN" \
                -H "Accept: application/vnd.github+json" \
                -H "X-GitHub-Api-Version: 2022-11-28"
}

jget() { # jget <json-file> <key>  -> first string value of key ("" if absent)
  "$PY" - "$1" "$2" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
val = data.get(sys.argv[2])
if isinstance(val, str):
    print(val)
PY
}

api_get() { curl -sS -m 60 -o "$1" -w "%{http_code}" "$@" ; }

ensure_release() {
  local tmp id upload base try
  tmp="$(mktemp)"
  for try in 1 2 3 4 5; do
    code=$(curl -sS -m 60 -o "$tmp" -w "%{http_code}" \
      -H "Authorization: Bearer $GH_TOKEN" \
      -H "Accept: application/vnd.github+json" \
      "$API/repos/$REPO/releases/tags/$TAG")
    if [ "$code" = "200" ]; then
      id=$(jget "$tmp" id); upload=$(jget "$tmp" upload_url)
      base="${upload%%\{*}"
      echo "release id=$id"
      echo "upload_base=$base"
      rm -f "$tmp"
      return 0
    fi
    # not found -> create it (another job may be doing the same right now).
    # BODY, if set, becomes the release description; python builds the payload so
    # quotes and newlines in it cannot break the JSON.
    curl -sS -m 60 -o "$tmp" \
      -H "Authorization: Bearer $GH_TOKEN" \
      -H "Accept: application/vnd.github+json" \
      -X POST "$API/repos/$REPO/releases" \
      -d "$("$PY" -c 'import json,os,sys; print(json.dumps({"tag_name": os.environ["GITHUB_REF_NAME"], "name": "ShunyaNet Sentinel " + os.environ["GITHUB_REF_NAME"], "generate_release_notes": True, "draft": False, **({"body": os.environ["BODY"]} if os.environ.get("BODY") else {})}))')" >/dev/null 2>&1
    echo "release for $TAG not ready (http $code); retry $try" >&2
    sleep $((try * 4))
  done
  echo "could not obtain a release for tag $TAG" >&2
  rm -f "$tmp"
  return 1
}

upload_asset() {
  local file="$1" name="${2:-$(basename "$1")}" tmp base code del
  [ -f "$file" ] || { echo "no such file: $file" >&2; return 1; }
  base="$(ensure_release | awk -F= '/^upload_base=/{print $2}')"
  [ -n "$base" ] || { echo "no upload url" >&2; return 1; }

  # replace an existing asset of the same name (re-running a tag must not 422)
  tmp="$(mktemp)"
  local rel_id="${base%%/assets*}"
  rel_id="${rel_id##*/}"
  curl -sS -m 60 -o "$tmp" \
    -H "Authorization: Bearer $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "$API/repos/$REPO/releases/$rel_id/assets?per_page=100" >/dev/null
  while read -r del; do
    [ -n "$del" ] || continue
    echo "replacing existing asset id $del"
    curl -sS -m 60 -X DELETE \
      -H "Authorization: Bearer $GH_TOKEN" \
      -H "Accept: application/vnd.github+json" \
      "$API/repos/$REPO/releases/assets/$del" >/dev/null
  done < <("$PY" - "$tmp" "$name" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
for a in (data.get("assets") or []):
    if a.get("name") == sys.argv[2]:
        print(a.get("id"))
PY
)
  rm -f "$tmp"

  local attempt
  for attempt in 1 2 3; do
    code=$(curl -sS -m 900 -o /dev/null -w "%{http_code}" \
      -H "Authorization: Bearer $GH_TOKEN" \
      -H "Accept: application/vnd.github+json" \
      -H "Content-Type: application/octet-stream" \
      --data-binary "@$file" \
      "$base?name=$name")
    case "$code" in
      201) echo "uploaded $name ($(wc -c < "$file") bytes)"; return 0 ;;
      *) echo "upload attempt $attempt for $name -> http $code" >&2; sleep $((attempt * 6)) ;;
    esac
  done
  return 1
}

case "${1:-}" in
  ensure) ensure_release ;;
  upload) shift; upload_asset "$@" ;;
  *) echo "usage: gh_release.sh ensure | upload <file> [asset-name]" >&2; exit 64 ;;
esac
