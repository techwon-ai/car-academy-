#!/usr/bin/env python3
"""For each Sunbiz candidate, look the business up on Google Maps by name + city.

Resumable: appends one JSON line per filing to OUT; already-done doc numbers are skipped.
Usage: python3 -I sunbiz_maps_lookup.py sunbiz.json out.jsonl
"""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import maps_api

STOP = {"LLC", "L", "C", "INC", "CORP", "CORPORATION", "CO", "COMPANY", "THE", "AND", "OF", "PA", "LLLP", "LP", "DBA", "SERVICES", "SERVICE"}


def toks(s):
    return [t for t in re.sub(r"[^A-Z0-9 ]", " ", s.upper().replace("&", " AND ")).split() if t not in STOP]


GENERIC = {"CLEANING", "CLEANERS", "CLEAN", "PEST", "CONTROL", "LAWN", "LAWNS", "CARE", "POOL", "POOLS", "ROOFING", "ROOF",
           "PLUMBING", "PLUMBER", "AIR", "HVAC", "CONDITIONING", "HEATING", "COOLING", "LANDSCAPING", "LANDSCAPE", "FL",
           "FLORIDA", "PRO", "PROS", "HOME", "HOMES", "COMMERCIAL", "RESIDENTIAL", "SOLUTIONS", "GROUP", "MAINTENANCE",
           "PRESSURE", "WASHING", "WASH", "SOFT", "REPAIR", "REPAIRS", "TREE", "SEPTIC", "DRAIN", "IRRIGATION", "PROPERTY",
           "EXPERTS", "SPA", "SPAS", "JANITORIAL", "MAID", "MAIDS", "OUTDOOR", "CONSTRUCTION", "CONTRACTING", "ENTERPRISES",
           "TERMITE", "EXTERMINATING", "AC", "MECHANICAL", "REFRIGERATION", "BAY", "SOUTH", "NORTH", "CENTRAL", "COAST", "GULF"}


def similarity(a, b):
    """Jaccard on distinctive tokens; a shared trade word alone ("PEST CONTROL") is not a match."""
    da, db = set(toks(a)) - GENERIC, set(toks(b)) - GENERIC
    if da and db:
        return len(da & db) / len(da | db)
    ta, tb = set(toks(a)), set(toks(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def clean_name(n):
    n = re.sub(r",?\s*(L\.?L\.?C\.?|INC\.?|CORP\.?|CORPORATION|CO\.|P\.?A\.?)\s*$", "", n.strip(), flags=re.I)
    return re.sub(r"\s+", " ", n)


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    done = set()
    if os.path.exists(out):
        done = {json.loads(l)["doc"] for l in open(out) if l.strip()}
    cands = [r for r in json.load(open(src)) if r["doc"] not in done]
    for i, r in enumerate(cands):
        q = f"{clean_name(r['name'])} {r['city'].title()} FL"
        rec = {"doc": r["doc"], "query": q, "match": None, "best_sim": 0}
        try:
            res = maps_api.search(q, 28.0, -81.7, zoom=7)
            scored = sorted(((similarity(r["name"], x["title"]), x) for x in res), key=lambda t: -t[0])
            if scored:
                sim, best = scored[0]
                rec["best_sim"] = round(sim, 2)
                # single answer = Google resolved the name itself; still require some token overlap
                # one shared word ("PRIME") is too weak on its own: also need 2+ distinctive words or the same city
                distinctive = set(toks(r["name"])) - GENERIC
                same_city = best["city"].upper().replace("SAINT", "ST.") == r["city"].upper().replace("SAINT", "ST.")
                if (sim >= 0.5 or (len(res) == 1 and sim >= 0.34)) and (len(distinctive) >= 2 or same_city):
                    rec["match"] = best
            rec["n_results"] = len(res)
            rec["candidates"] = [{k: x[k] for k in ("title", "phone", "website", "reviews", "city", "maps_url")} for _, x in scored[:5]]
        except Exception as e:
            rec["error"] = str(e)[:150]
        with open(out, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        if i % 50 == 0:
            print(i, len(cands), flush=True)
        maps_api.pause(1.5, 3.0)
