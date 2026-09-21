#!/usr/bin/env python3
"""Build a California roofer lead list from OpenStreetMap.

This is a fallback for environments where the Google Maps scraper cannot run
(for example a sandbox whose proxy blocks Google's Maps JavaScript bundle).
It is NOT a replacement: OpenStreetMap's coverage of small US trades is far
thinner than Google Maps, so expect tens of businesses, not hundreds.

Data source: OpenStreetMap via the Overpass API. OSM data is licensed ODbL
(https://www.openstreetmap.org/copyright) — attribute it if you redistribute.

Usage:
    python3 fetch-osm-roofers.py [STATE_ISO] [OUT_CSV]
    python3 fetch-osm-roofers.py US-CA
    python3 fetch-osm-roofers.py US-TX texas-roofers.csv

Standard library only.

IMPORTANT — the website column:
    A blank `website` means OpenStreetMap has no website recorded, which is NOT
    evidence the business lacks one. OSM is volunteer-maintained and most
    business tags are incomplete. Do not treat a blank as a "no website" lead.
    The `website_status` column states this explicitly per row.
"""
import csv, datetime, json, re, sys, time, urllib.error, urllib.parse, urllib.request

# Overpass is a free shared service; instances rate-limit and time out under load,
# so try more than one and retry before giving up.
ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
STATE = sys.argv[1] if len(sys.argv) > 1 else "US-CA"

DIRECT = f"""[out:json][timeout:240];
area["ISO3166-2"="{STATE}"]->.a;
(
  nwr["craft"="roofer"](area.a);
  nwr["shop"="roofing"](area.a);
  nwr["craft"="roofing"](area.a);
);
out center tags;"""

# Name regex over a whole state times out on Overpass, so query indexed
# contractor tags and filter for roofing client-side instead.
BROAD = f"""[out:json][timeout:240];
area["ISO3166-2"="{STATE}"]->.a;
(
  nwr["office"="contractor"](area.a);
  nwr["shop"="trade"](area.a);
);
out center tags;"""


def overpass(query, attempts=2):
    """POST a query, trying each endpoint with backoff. Raises if all fail."""
    payload = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(attempts):
        for url in ENDPOINTS:
            try:
                req = urllib.request.Request(url, data=payload)
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.load(r).get("elements", [])
            except (urllib.error.HTTPError, urllib.error.URLError,
                    TimeoutError, json.JSONDecodeError) as e:
                last = f"{url.split('/')[2]}: {e}"
                print(f"    {last}")
        if attempt + 1 < attempts:
            wait = 10 * (attempt + 1)
            print(f"    all endpoints failed; retrying in {wait}s ...")
            time.sleep(wait)
    raise RuntimeError(f"Overpass unavailable (last error: {last})")


def tag(el, *keys):
    tags = el.get("tags") or {}
    for k in keys:
        if tags.get(k):
            return tags[k].strip()
    return ""


def main():
    print(f"querying OpenStreetMap for roofers in {STATE} ...")
    elements = overpass(DIRECT)
    print(f"  direct roofer tags: {len(elements)}")
    # Best-effort: this one scans every contractor in the state and is the first
    # to time out. It typically adds only a handful, so never let it fail the run.
    try:
        broad = [e for e in overpass(BROAD, attempts=1)
                 if re.search(r"roof", tag(e, "name"), re.I)]
        print(f"  contractor tags matching /roof/i: {len(broad)}")
        elements += broad
    except RuntimeError as e:
        print(f"  skipping broad contractor sweep ({e})")

    rows, seen = [], set()
    for el in elements:
        name = tag(el, "name")
        if not name:
            continue
        key = (name.lower(), re.sub(r"[\s-]", "", tag(el, "phone", "contact:phone")))
        if key in seen:
            continue
        seen.add(key)

        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        state_code = STATE.split("-")[-1]
        street = " ".join(x for x in [tag(el, "addr:housenumber"), tag(el, "addr:street")] if x)
        postcode = tag(el, "addr:postcode")
        address = ", ".join(x for x in [
            street, tag(el, "addr:city"),
            (f"{state_code} {postcode}".strip() if postcode else state_code)] if x)
        website = tag(el, "website", "contact:website")

        if name and street:
            maps = ("https://www.google.com/maps/search/?api=1&query="
                    + urllib.parse.quote(f"{name} {address}"))
        elif lat and lon:
            maps = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        else:
            maps = ""

        rows.append({
            "title": name,
            "category": tag(el, "craft") or tag(el, "shop") or tag(el, "office") or "roofer",
            "phone_number": tag(el, "phone", "contact:phone"),
            "website": website,
            "email_address": tag(el, "email", "contact:email"),
            "review_count": "",      # OpenStreetMap has no review data
            "review_rating": "",     # OpenStreetMap has no review data
            "google_maps_link": maps,  # constructed from name/address or coordinates
            "address": address,
            "website_status": "has website" if website
                              else "not recorded in OpenStreetMap (unverified)",
            "source": f"OpenStreetMap {el.get('type')}/{el.get('id')}",
            "osm_url": f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
        })

    # Businesses with no recorded website first — most likely to be worth a look.
    rows.sort(key=lambda r: (r["website"] != "", r["title"].lower()))

    out = (sys.argv[2] if len(sys.argv) > 2
           else f"roofers-{state_code.lower()}-openstreetmap-{datetime.date.today().isoformat()}.csv")
    cols = ["title", "category", "phone_number", "website", "email_address",
            "review_count", "review_rating", "google_maps_link", "address",
            "website_status", "source", "osm_url"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    no_web = [r for r in rows if not r["website"]]
    print(f"\nwrote {out}")
    print(f"  businesses        : {len(rows)}")
    print(f"  with phone        : {sum(1 for r in rows if r['phone_number'])}")
    print(f"  with address      : {sum(1 for r in rows if r['address'])}")
    print(f"  with email        : {sum(1 for r in rows if r['email_address'])}")
    print(f"  website recorded  : {sum(1 for r in rows if r['website'])}")
    print(f"  website not recorded: {len(no_web)} (unverified — not proof they lack one)")
    print("\nOpenStreetMap contributors, ODbL: https://www.openstreetmap.org/copyright")


if __name__ == "__main__":
    main()
