#!/usr/bin/env bash
# Google Maps via gosom/google-maps-scraper in -fast-mode, one run per city (fast mode takes one geo point).
# Normal mode returns blank fields in this environment; fast mode returns full records.
set -u
cd "$(dirname "$0")/../.."
export PLAYWRIGHT_NODEJS_PATH=${PLAYWRIGHT_NODEJS_PATH:-$(command -v node)}
B=.local-lead-scraper/bin/google-maps-scraper
while IFS=$'\t' read -r slug name geo; do
  out=output/maps/$slug.csv
  [ -s "$out" ] && { echo "skip $slug"; continue; }
  sed "s/\$/ in $name FL/" leadgen/maps/queries.txt > output/maps/$slug.queries
  echo "$(date +%T) start $slug"
  timeout 3000 $B -input output/maps/$slug.queries -results "$out" -fast-mode -geo "$geo" -zoom 12 -radius 30000 -depth 1 -email -c 2 -exit-on-inactivity 3m >output/maps/$slug.log 2>&1
  echo "$(date +%T) done $slug rows=$(($(wc -l <"$out")-1))"
  sleep 20
done < leadgen/maps/cities.tsv
echo ALL_DONE
