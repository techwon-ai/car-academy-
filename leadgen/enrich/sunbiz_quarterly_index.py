#!/usr/bin/env python3
"""Stream the Sunbiz quarterly corporate file (cordata.zip, ~18GB unzipped) and keep active Florida
entities whose names match an ICP trade, with officers. Used to find owner names for Maps listings.
cordata.zip uses Deflate64, which Python's zipfile cannot read, so each member is piped through unzip -p.
Usage: python3 -I sunbiz_quarterly_index.py cordata.zip out.json   (needs the unzip CLI)
"""
import io, json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from trades import classify
from sunbiz_parse import TITLES, display_name, f, mdY
QUICK = re.compile(r"ROOF|HVAC|AIR|HEAT|COOL|REFRIG|DUCT|PLUMB|DRAIN|SEPTIC|ROOTER|LEAK|LANDSCAP|LAWN|TREE|IRRIGAT|\bSOD\b|HARDSCAP|MOW|YARD|CLEAN|MAID|JANITOR|HOUSEKEEP|PRESSURE|POWER|SOFT|WASH|PEST|EXTERMIN|TERMITE|MOSQUITO|BUG|WILDLIFE|POOL|SPA\b|A/C")


def entities(raw_lines):
    for raw in raw_lines:
        if raw[204:205] != "A":
            continue
        name = raw[12:204].strip()
        if not QUICK.search(name):
            continue
        trade = classify(name)
        if not trade:
            continue
        offs = []
        for i in range(6):
            b = 669 + 128 * i
            nm = f(raw, b + 5, 42)
            if nm:
                typ = f(raw, b + 4, 1)
                offs.append({"title": TITLES.get(f(raw, b, 4), f(raw, b, 4)), "type": typ, "name": " ".join(nm.split()),
                             "display": display_name(raw[b + 4:b + 46]) if typ == "P" else nm})
        yield {"doc": f(raw, 1, 12), "name": name, "trade": trade, "city": f(raw, 305, 28), "zip": f(raw, 335, 10)[:5],
               "filed": mdY(f(raw, 473, 8)), "officers": offs, "ra": " ".join(f(raw, 545, 42).split())}


if __name__ == "__main__":
    out = []
    for i in range(10):
        member = f"cordata{i}.txt"
        proc = subprocess.Popen(["unzip", "-p", sys.argv[1], member], stdout=subprocess.PIPE)
        out.extend(entities(io.TextIOWrapper(proc.stdout, encoding="latin1")))
        proc.wait()
        print(member, len(out), flush=True)
    json.dump(out, open(sys.argv[2], "w"))
