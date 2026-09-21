#!/usr/bin/env bash
# Roofers across California with no website listed — target 200 leads.
# Run from your project root on a machine with unrestricted network access.
set -euo pipefail

BIN="./.local-lead-scraper/bin/google-maps-scraper"
[ -x "$BIN" ] || { echo "Scraper not found at $BIN — ask Claude to run first-run setup."; exit 1; }
mkdir -p output

# 93 queries across 69 California cities at depth 15.
# Most roofers DO list a website, so reaching 200 genuine "no website" matches
# needs a raw pool in the high hundreds to low thousands. This is a long run.
"$BIN" \
  -input queries-roofers-california.txt \
  -results output/raw-roofers-california.csv \
  -depth 15 \
  -c 2 \
  -lang en \
  -email \
  -exit-on-inactivity 5m

echo "Raw scrape complete: output/raw-roofers-california.csv"
echo "Raw rows: $(( $(wc -l < output/raw-roofers-california.csv) - 1 ))"
echo "Next: python3 clean-roofers-california.py"
