#!/usr/bin/env bash
# youtube_demo_report.sh
# By-date report of demos built from the YouTube -> tutorial -> demo pipeline.
# Reads the code-verified layout: /opt/ua_demos/<slug>__demo-N is a symlink into
# the real build workspace; each demo carries a manifest.json with the source
# video_id / video_title / video_url / endpoint_hit / timestamp / build_kind
# (see services/tutorial_demo_finalize.py::finalize_tutorial_build_demo).
#
# Run on the VPS:   ssh ua@uaonvps 'bash -s' < youtube_demo_report.sh
# Or in place:      bash youtube_demo_report.sh
# Optional window:  SINCE=2026-06-15  UNTIL=2026-06-19  bash youtube_demo_report.sh
set -uo pipefail

ROOT="${UA_DEMOS_ROOT:-/opt/ua_demos}"
SINCE="${SINCE:-2026-06-15}"     # Jun 15 = Sunday-playlist digest (per operator request)
UNTIL="${UNTIL:-2026-06-19}"

command -v jq >/dev/null || { echo "jq not found"; exit 1; }
[ -d "$ROOT" ] || { echo "demos root not found: $ROOT"; exit 1; }

rows="$(
  for d in "$ROOT"/*; do
    [ -e "$d" ] || continue
    target="$(readlink -f "$d" 2>/dev/null || echo "$d")"
    m=""
    for cand in "$d/manifest.json" "$d/work_products/manifest.json" \
                "$target/manifest.json" "$target/work_products/manifest.json"; do
      [ -f "$cand" ] && { m="$cand"; break; }
    done
    [ -n "$m" ] || continue
    jq -r --arg link "$d" --arg tgt "$target" '
      [ ((.timestamp // .finished_at // "0000-00-00")|tostring|.[0:10]),
        $link, $tgt,
        (.endpoint_hit // "?"), (.build_kind // "?"),
        (.video_title // ""), (.video_id // ""), (.video_url // "") ] | @tsv
    ' "$m" 2>/dev/null
  done | sort
)"

# Filter to the requested window and group by date.
printf '%s\n' "$rows" \
| awk -F'\t' -v since="$SINCE" -v until="$UNTIL" '
    $1=="" { next }
    ($1>=since && $1<=until) {
      if ($1!=prev) { printf "\n=== %s ===\n", $1; prev=$1 }
      printf "  - %s\n", ($6==""?"(no video title in manifest)":$6)
      printf "      dir:      %s\n", $2
      printf "      target:   %s\n", $3
      printf "      endpoint: %s    build_kind: %s\n", $4, $5
      if ($7!="" || $8!="") printf "      video:    %s  %s\n", $7, $8
      n++
    }
    END { printf "\n%d demo(s) in %s..%s\n", n, since, until }
  '
