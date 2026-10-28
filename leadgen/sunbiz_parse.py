#!/usr/bin/env python3
"""Parse Sunbiz daily corporate (c) and fictitious-name (f) files into trade-matched candidates.

Layouts: https://dos.sunbiz.org/data-definitions/cor.html and fic.html (fixed width, 1-indexed).
Usage: python3 -I sunbiz_parse.py <dir with yyyymmddc.txt / yyyymmddf.txt> <out.json>
"""
import glob, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trades import classify

def f(rec, start, length):
    # records are read as latin1 so offsets stay byte positions; the bytes themselves are UTF-8
    return fix(rec[start - 1:start - 1 + length]).replace("\x00", " ").strip()


def fix(s):
    try:
        return s.encode("latin1").decode("utf-8")
    except UnicodeError:
        return s

def mdY(s):
    return f"{s[4:8]}-{s[0:2]}-{s[2:4]}" if len(s) == 8 and s.isdigit() else ""

def display_name(raw):
    """Person names are fixed-width: last [0:20], first [20:34], middle [34:]. Keeps double surnames intact."""
    raw = fix(raw).replace("\x00", " ")
    last, first, middle = raw[:20].strip(), raw[20:34].strip(), raw[34:].strip()
    if not first:  # unstructured value; leave as written
        return " ".join(raw.split()).title()
    suffix = ""
    m = re.fullmatch(r"([A-Z]?)\.?\s*(JR|SR|II|III|IV)\.?", middle.upper())
    if m:  # "JR", "A JR", "AJR" -> middle initial + suffix
        middle, suffix = m.group(1), m.group(2).title() if m.group(2) in ("JR", "SR") else m.group(2)
    return " ".join(p for p in (first.title(), middle.title() if len(middle) > 1 else middle, last.title(), suffix) if p)


TITLES = {"P": "President", "T": "Treasurer", "C": "Chairman", "V": "Vice President", "S": "Secretary",
          "D": "Director", "MGR": "Manager", "AMBR": "Authorized Member", "MGRM": "Managing Member",
          "AP": "Authorized Person", "CEO": "CEO", "PRES": "President", "VP": "Vice President"}

def parse_cor(path, out):
    for rec in open(path, encoding="latin1"):
        if len(rec) < 600:
            continue
        name = f(rec, 13, 192)
        trade = classify(name)
        if not trade:
            continue
        ftype, status = f(rec, 206, 15), f(rec, 205, 1)
        if ftype not in ("FLAL", "DOMP") or status != "A":
            continue  # foreign filings are existing out-of-state companies, not new businesses
        filed = mdY(f(rec, 473, 8))
        officers = []
        for i in range(6):
            b = 669 + 128 * i
            nm = f(rec, b + 5, 42)
            if not nm:
                continue
            typ = f(rec, b + 4, 1)
            title = f(rec, b, 4)
            raw = rec[b + 4:b + 4 + 42]
            officers.append({"title": TITLES.get(title, title), "type": typ, "name": " ".join(nm.split()),
                             "display": display_name(raw) if typ == "P" else nm,
                             "city": f(rec, b + 89, 28), "state": f(rec, b + 117, 2)})
        ra = " ".join(f(rec, 545, 42).split())
        out.append({"doc": f(rec, 1, 12), "name": name, "kind": "corp", "filing_type": ftype,
                    "filed": filed, "trade": trade,
                    "addr": f(rec, 221, 42), "addr2": f(rec, 263, 42), "city": f(rec, 305, 28),
                    "state": f(rec, 333, 2) or "FL", "zip": f(rec, 335, 10)[:5],
                    "officers": officers, "registered_agent": ra, "file": os.path.basename(path)})

def parse_fic(path, out):
    for rec in open(path, encoding="latin1"):
        if len(rec) < 500:
            continue
        name = f(rec, 13, 192)
        trade = classify(name)
        if not trade or f(rec, 352, 1) != "A":
            continue
        owners = []
        for i in range(10):
            b = 389 + 171 * i
            nm = f(rec, b + 12, 55)
            if nm:
                typ = f(rec, b + 67, 1)
                owners.append({"title": "Owner", "type": typ, "name": " ".join(nm.split()),
                               "display": display_name(rec[b + 11:b + 66]) if typ == "P" else nm,
                               "city": f(rec, b + 108, 28), "state": f(rec, b + 136, 2)})
        out.append({"doc": f(rec, 1, 12), "name": name, "kind": "fictitious", "filing_type": "FICT",
                    "filed": mdY(f(rec, 339, 8)), "trade": trade, "county": f(rec, 205, 12),
                    "addr": f(rec, 217, 40), "addr2": f(rec, 257, 40), "city": f(rec, 297, 28),
                    "state": f(rec, 325, 2) or "FL", "zip": f(rec, 327, 10)[:5],
                    "officers": owners, "registered_agent": "", "file": os.path.basename(path)})

if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    out = []
    for p in sorted(glob.glob(os.path.join(src, "*c.txt"))):
        parse_cor(p, out)
    for p in sorted(glob.glob(os.path.join(src, "*f.txt"))):
        parse_fic(p, out)
    seen, uniq = set(), []
    for r in out:
        if r["doc"] not in seen:
            seen.add(r["doc"]); uniq.append(r)
    json.dump(uniq, open(dst, "w"), indent=1)
    from collections import Counter
    print(len(uniq), Counter(r["trade"] for r in uniq), Counter(r["kind"] for r in uniq))
