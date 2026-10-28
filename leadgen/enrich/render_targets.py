#!/usr/bin/env python3
"""Pick the sites that need a rendered audit: anything the quick HTML audit could not clear, anything it
never saw, plus sites from Sunbiz matches and curated sources. Sites that clearly pass the quick check
(viewport, call/quote path, fast, real content) are skipped.
Usage: python3 -I render_targets.py out.txt
"""
import csv, glob, json, os, re, sys
W = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "output", "work")
M = os.path.join(W, "..", "maps")
jl = lambda p: [json.loads(l) for l in open(p) if l.strip()] if os.path.exists(p) else []
static = {a["url"]: a for p in ("audit.jsonl", "audit2.jsonl") for a in jl(os.path.join(W, p))}
SKIP = re.compile(r"facebook\.com|instagram\.com|linktr\.ee|yelp\.com|nextdoor|thumbtack|angi\.com|homeadvisor", re.I)
strip = lambda u: re.sub(r"[?&](utm_[^&]+|gclid=[^&]+|y_source=[^&]+)", "", u or "").rstrip("?&")
sites = set()
for rec in jl(os.path.join(W, "maps_paged.jsonl")):
    sites |= {x["website"] for x in rec["results"] if x["website"] and x["reviews"] < 500}
for p in glob.glob(os.path.join(M, "*.csv")):
    sites |= {r["website"] for r in csv.DictReader(open(p)) if r["website"]}
for d in jl(os.path.join(W, "sunbiz_maps.jsonl")):
    for c in d.get("candidates") or []:
        if c["website"]:
            sites.add(c["website"])
for v in json.load(open(os.path.join(W, "curated_lookup.json"))).values():
    if v["maps"].get("website"):
        sites.add(v["maps"]["website"])
for d in ["tallahasseeroofrejuvenation.com", "mikeplumbingfl.com", "bellacleaningfl.com", "downtownorlandoplumbing.com",
          "mendietaroofingcorppalmbeach.com", "shinefloridalawncare.com", "asapplumbingdraintampa.com", "treasurecoastpowerwashing.com", "northfloridavaultseptic.com"]:
    sites.add("https://" + d)
out = []
for s in sorted(sites):
    if SKIP.search(s):
        continue
    a = static.get(s) or static.get(strip(s))
    clean_pass = (a and a.get("loads") and a.get("viewport") and not a.get("parked") and a.get("seconds", 9) < 3
                  and (a.get("tel_link") or a.get("form") or a.get("booking")) and a.get("words", 0) > 150)
    if not clean_pass:
        out.append(s)
open(sys.argv[1], "w").write("\n".join(out))
print(len(sites), "sites;", len(out), "need a rendered audit")
