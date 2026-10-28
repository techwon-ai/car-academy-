#!/usr/bin/env python3
"""Deeper Google Maps coverage: trade queries x city geos x result pages (offset 0/20/40).

Resumable JSONL output, one line per (query, city, offset) with the parsed businesses.
Usage: python3 -I maps_paged.py out.jsonl
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import maps_api

out = sys.argv[1]
done = set()
if os.path.exists(out):
    done = {json.loads(l)["job"] for l in open(out) if l.strip()}
queries = [q.strip() for q in open(os.path.join(HERE, "queries.txt")) if q.strip()]
cities = [l.rstrip("\n").split("\t") for l in open(os.path.join(HERE, "cities.tsv")) if l.strip()]
for slug, name, geo in cities:
    lat, lon = map(float, geo.split(","))
    for q in queries:
        for off in (0, 20, 40):
            job = f"{slug}|{q}|{off}"
            if job in done:
                continue
            try:
                res = maps_api.search(f"{q} in {name} FL", lat, lon, zoom=12, offset=off)
                rec = {"job": job, "city": name, "query": q, "offset": off, "results": res}
            except Exception as e:
                rec = {"job": job, "city": name, "query": q, "offset": off, "results": [], "error": str(e)[:150]}
            with open(out, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            maps_api.pause(1.2, 2.8)
            if len(rec["results"]) < 20:
                break  # no further pages
    print("city done", slug, flush=True)
