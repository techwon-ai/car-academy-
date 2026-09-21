#!/usr/bin/env bash
# Roofers in California with no website listed — target 50 leads.
# Run from your project root on a machine with unrestricted network access.
set -euo pipefail

BIN="./.local-lead-scraper/bin/google-maps-scraper"
mkdir -p output

# depth 10 per query across 30 queries -> several hundred raw listings.
# Only a minority of roofers lack a website, so a wide raw pool is needed
# to reach 50 genuine "no website" matches without relaxing the filter.
"$BIN" \
  -input queries-roofers-california.txt \
  -results output/raw-roofers-california.csv \
  -depth 10 \
  -c 2 \
  -lang en \
  -email \
  -exit-on-inactivity 3m

echo "Raw scrape complete: output/raw-roofers-california.csv"
echo "Rows: $(( $(wc -l < output/raw-roofers-california.csv) - 1 ))"
