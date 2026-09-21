#!/usr/bin/env python3
"""Turn the raw Google Maps scrape into the final 50-lead CSV.

Filter: roofers in California with NO website listed.

Guard that matters: a blank `website` only counts as "no website listed" when the
listing actually extracted. If the row has no business name, extraction failed and
the blank is UNKNOWN, not absent -- those rows are dropped, never counted as matches.
"""
import csv, datetime, re, sys
from urllib.parse import urlparse

RAW = sys.argv[1] if len(sys.argv) > 1 else "output/raw-roofers-california.csv"
TARGET = 50

STANDARD = ["title", "category", "phone_number", "website", "email_address",
            "review_count", "review_rating", "google_maps_link"]
FINAL = STANDARD + ["address", "why_matched"]

def first_email(v):
    v = (v or "").strip()
    if not v or v == "[]":
        return ""
    return re.split(r"[,;\s]+", v.strip("[]\"' "))[0].strip("\"' ")

rows = list(csv.DictReader(open(RAW, encoding="utf-8")))
print(f"raw rows: {len(rows)}")

extracted, unextracted = [], 0
for r in rows:
    if not (r.get("title") or "").strip():
        unextracted += 1          # extraction failed -> website is UNKNOWN
        continue
    extracted.append(r)
print(f"extraction failed (dropped, website unknown): {unextracted}")
print(f"usable listings: {len(extracted)}")

seen, deduped = set(), []
for r in extracted:
    key = ((r.get("place_id") or "").strip() or (r.get("cid") or "").strip()
           or (r.get("phone") or "").strip()
           or (urlparse((r.get("website") or "").strip()).netloc.lower() or None)
           or (r.get("title", "") + "|" + r.get("address", "")).strip())
    if key in seen:
        continue
    seen.add(key); deduped.append(r)
print(f"after dedupe: {len(deduped)}")

matches = [r for r in deduped if not (r.get("website") or "").strip()]
print(f"no website listed: {len(matches)}")

out = []
for r in matches[:TARGET]:
    out.append({
        "title": r.get("title", ""), "category": r.get("category", ""),
        "phone_number": r.get("phone", ""), "website": "",
        "email_address": first_email(r.get("emails")),
        "review_count": r.get("review_count", ""), "review_rating": r.get("review_rating", ""),
        "google_maps_link": r.get("link", ""), "address": r.get("address", ""),
        "why_matched": "No website is listed on the Google Maps profile.",
    })

path = f"leads-roofers-california-{datetime.date.today().isoformat()}.csv"
with open(path, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=FINAL); w.writeheader(); w.writerows(out)

assert all(c in FINAL for c in STANDARD), "mandatory column missing"
print(f"\nSaved {len(out)} leads -> {path}")
print(f"  with phone   : {sum(1 for r in out if r['phone_number'].strip())}")
print(f"  with address : {sum(1 for r in out if r['address'].strip())}")
print(f"  with email   : {sum(1 for r in out if r['email_address'].strip())}")
if len(out) < TARGET:
    print(f"\nOnly {len(out)} of {TARGET} requested matched. Location and filters were NOT "
          f"broadened or relaxed. Expand metros or loosen a filter only if you want that.")
