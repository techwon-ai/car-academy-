#!/usr/bin/env python3
"""Index Florida DBPR construction licenses (free extract) by business name -> licensee (qualifier) names.
Only active licenses in ICP trades: roofing (CCC/RC), A/C & mechanical (CAC/RA/CMC/RM), plumbing (CFC/RF),
pool (CPC/RP and pool/spa servicing RS?). The licensee is the license holder/qualifier, usually but not
always the owner, so names from here get medium confidence.
Usage: python3 -I dbpr_index.py CONSTRUCTIONLICENSE_1.csv out.json
"""
import csv, json, re, sys
TYPES = {"CCC": "roofing", "RC": "roofing", "CAC": "hvac", "RA": "hvac", "CMC": "hvac", "RM": "hvac",
         "CFC": "plumbing", "RF": "plumbing", "CPC": "pool_service", "RP": "pool_service", "RS": "pool_service", "CSC": "pool_service"}


def norm(s):
    s = re.sub(r"[^A-Z0-9 ]", " ", s.upper().replace("&", " AND "))
    s = re.sub(r"\b(LLC|L L C|INC|CORP|CORPORATION|CO|COMPANY|THE|OF|PA|DBA)\b", " ", s)
    return " ".join(s.split())


idx = {}
for row in csv.reader(open(sys.argv[1], encoding="latin1")):
    if len(row) < 21 or row[1] not in TYPES or row[14] != "A":
        continue
    person, biz = row[2].strip(), row[3].strip()
    if not biz:
        continue
    last, _, first = person.partition(",")
    name = " ".join(f"{first.strip()} {last.strip()}".split()).title()
    idx.setdefault(norm(biz), []).append({"name": name, "license": row[20], "type": row[1], "city": row[8], "trade": TYPES[row[1]]})
json.dump(idx, open(sys.argv[2], "w"))
print(len(idx), "businesses")
