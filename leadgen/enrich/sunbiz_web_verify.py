#!/usr/bin/env python3
"""Web check for Sunbiz filings that have no Google Maps match, so "no website" is a finding, not a guess.

  prep:    python3 -I sunbiz_web_verify.py prep sunbiz_fl.json sunbiz_maps.jsonl queries.json [limit]
  verdict: python3 -I sunbiz_web_verify.py verdict sunbiz_fl.json brave.jsonl out.jsonl

A result counts as the business's own site when its domain contains the filing's distinctive name
tokens (e.g. hagoodlookinlawncare.com). Directory/aggregator pages (Bizapedia, OpenCorporates, Yelp,
HomeAdvisor...) are recorded as presence but are not a website.
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sunbiz_maps_lookup import GENERIC, clean_name, toks

METRO = re.compile(r"TAMPA|BRANDON|RIVERVIEW|WESLEY|LUTZ|ORLANDO|KISSIMMEE|SANFORD|WINTER|OVIEDO|APOPKA|CLERMONT|OCOEE|JACKSONVILLE|ORANGE PARK|"
                   r"ST\.? ?JOHNS|MIAMI|HIALEAH|DORAL|HOMESTEAD|KENDALL|LAUDERDALE|HOLLYWOOD|POMPANO|CORAL SPRINGS|PEMBROKE|PLANTATION|"
                   r"DAVIE|SUNRISE|MIRAMAR|PETERSBURG|CLEARWATER|LARGO|PINELLAS|SARASOTA|BRADENTON|VENICE|FORT MYERS|CAPE CORAL|"
                   r"LEHIGH|NAPLES|LAKELAND|WINTER HAVEN|TALLAHASSEE", re.I)
DIRECTORY = re.compile(r"bizapedia|opencorporates|sunbiz|buzzfile|yelp|facebook|instagram|linkedin|homeadvisor|angi|thumbtack|"
                       r"nextdoor|bbb\.org|mapquest|manta|dnb\.com|zoominfo|corporationwiki|bisprofiles|chamberofcommerce|"
                       r"yellowpages|superpages|porch|houzz|dayonelead|floridabizdb|companiesfl|flcompanies|govt|\.gov|"
                       r"youtube|tiktok|pinterest|indeed|glassdoor|ziprecruiter|reddit|google\.|birdeye|alignable|expertise|"
                       r"cylex|hotfrog|merchantcircle|brownbook|ezlocal|local\.com|citysearch|n49|showmelocal", re.I)


def distinct(name):
    return [t.lower() for t in toks(clean_name(name)) if t not in GENERIC]


if sys.argv[1] == "prep":
    sb = {r["doc"]: r for r in json.load(open(sys.argv[2]))}
    look = [json.loads(l) for l in open(sys.argv[3]) if l.strip()]
    limit = int(sys.argv[5]) if len(sys.argv) > 5 else 250
    todo = []
    for d in look:
        r = sb.get(d["doc"])
        if not r or d.get("match") or r["filed"] < "2026-09-01":
            continue
        if d.get("n_results") == 1 and d.get("best_sim", 0) >= 0.99:
            continue  # exact single Maps answer: treated as found
        if r["kind"] == "fictitious" and not any(o.get("type") == "P" for o in r["officers"]):
            continue
        todo.append(r)
    todo.sort(key=lambda r: (0 if METRO.search(r["city"]) else 1, r["filed"]), reverse=False)
    qs = [{"key": r["doc"], "q": f'"{clean_name(r["name"]).title()}" {r["city"].title()} FL'} for r in todo[:limit]]
    json.dump(qs, open(sys.argv[4], "w"))
    print(len(todo), "unmatched;", len(qs), "queued")
else:
    sb = {r["doc"]: r for r in json.load(open(sys.argv[2]))}
    with open(sys.argv[4], "w") as out:
        for l in open(sys.argv[3]):
            d = json.loads(l)
            if not d.get("q"):
                continue
            r = sb[d["key"]]
            dt = distinct(r["name"])
            joined = "".join(dt)
            v = {"key": d["key"], "verdict": "unknown" if (d.get("blocked") or d.get("error")) else "no_site_found", "url": "", "presence": []}
            for res in d.get("results", []):
                dom = re.sub(r"^www\.", "", re.sub(r"^https?://", "", res["url"]).split("/")[0].lower())
                if DIRECTORY.search(dom):
                    if any(t in res["text"].lower() for t in dt):
                        v["presence"].append(dom)
                    continue
                if joined and (joined in dom.replace("-", "") or (len(dt) >= 2 and all(t in dom for t in dt[:2]))):
                    v.update(verdict="site_found", url=res["url"])
                    break
            out.write(json.dumps(v) + "\n")
