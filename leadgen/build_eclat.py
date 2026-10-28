#!/usr/bin/env python3
"""Eclat sheet: only brand-new Florida home-service businesses that are already live on Google with a
verified phone, still have no real website, and score a full 7.

A row is kept only if ALL of these hold:
  - registered with Sunbiz (new LLC/corp, or a DBA filed by a person) within the window, Florida address
  - a Google Maps listing matches the filing on identical distinctive name words AND (same city OR Google
    returned that single business); a fuzzy or partial match is never accepted
  - the listing phone is a Florida area code and no other business name in our 6,600-listing Maps corpus uses it
  - the listing has at most 25 reviews (more means an established business that merely re-filed paperwork)
  - no website, or the website is broken / parked / only a social page / fails the rendered mobile audit
  - the profile is still bare (0 reviews or no hours listed)
  - a named person officer/owner on the filing
  - a live re-check of the listing just before writing: same listing still returned, same phone, and no
    "Temporarily closed" / "Permanently closed" text anywhere in the listing data
Score (the original rubric): +3 new filing, +2 no/broken site or +2 failing site, +1 bare profile,
+1 owner and phone = 7. Anything below 7 is dropped.

Not verifiable from this environment and therefore NOT claimed: social-media post dates (Facebook redirects
to login, Instagram returns 429) and Google review dates (the reviews endpoint returns 403).

Usage: python3 -I build_eclat.py <scratch dir>
"""
import csv, glob, json, os, re, sys, time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "leadgen"))
sys.path.insert(0, os.path.join(ROOT, "leadgen", "enrich"))
import maps_api
from trades import classify
from sunbiz_maps_lookup import GENERIC, toks

W = os.path.join(ROOT, "output", "work")
SCRATCH = sys.argv[1]
WINDOW_START = "2026-08-25"
COLS = ["lead_id", "source", "source_url", "trigger_signal", "trigger_date", "business_name", "trade", "city", "state",
        "owner_name", "owner_name_confidence", "owner_phone", "business_phone", "phone_confidence", "email", "website",
        "website_status", "google_maps_url", "facebook", "instagram", "linkedin", "gap_findings", "score",
        "primary_pitch", "pitch_hook", "notes"]
FL_AREA = {"239", "305", "321", "324", "352", "386", "407", "448", "561", "645", "656", "689", "727", "728", "754", "772",
           "786", "813", "850", "863", "904", "941", "954"}
TRADE_PLURAL = {"roofing": "roofers", "hvac": "AC companies", "plumbing": "plumbers", "landscaping": "lawn care companies",
                "cleaning": "cleaners", "pressure_washing": "pressure washing companies", "pest_control": "pest control companies",
                "pool_service": "pool services"}
SITE_PHRASE = {"social/profile page only": "its website link only goes to a social page",
               "parked/under construction": "its website is still a placeholder page",
               "broken": "its website doesn't load",
               "loads; fails audit": "its website doesn't work properly on a phone"}
SOCIAL = re.compile(r"facebook\.com|instagram\.com|linktr\.ee|yelp\.com|nextdoor|business\.site|thumbtack|angi\.com|"
                    r"homeadvisor|docs\.google\.com|forms\.gle|g\.page", re.I)
CLOSED = re.compile(r"(temporarily|permanently) closed|closed permanently", re.I)
jl = lambda p: [json.loads(l) for l in open(p) if l.strip()] if os.path.exists(p) else []
stats = Counter()


def distinct(name):
    return set(toks(re.sub(r",?\s*(L\.?L\.?C\.?|INC\.?|CORP\.?)\s*$", "", name or "", flags=re.I))) - GENERIC


def norm_city(c):
    c = (c or "").upper().replace("SAINT ", "ST ").replace("ST. ", "ST ").replace("FT. ", "FORT ").replace("FT ", "FORT ")
    return re.sub(r"[^A-Z ]", "", c).strip()


def digits(p):
    return re.sub(r"\D", "", p or "")[-10:]


def fmt_phone(p):
    d = digits(p)
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else ""


def nice(name):
    """'CLEAN & LUMINOUS LLC' -> 'Clean & Luminous LLC'; keeps suffixes and vowel-less initialisms (JB, A&A) upper-case."""
    words = []
    for w in name.split():
        stem = re.sub(r"['’]S$", "", w.upper())  # judge "TJ'S" by "TJ"
        core = re.sub(r"[^A-Z]", "", stem)
        if core in {"LLC", "LLP", "HVAC", "AC", "USA", "FL", "II", "III", "PA"} or (core and len(core) <= 3 and not re.search(r"[AEIOUY]", core)) or re.fullmatch(r"[A-Z]&[A-Z]", stem):
            out = stem
        elif core == "INC":
            out = "Inc."
        else:
            out = re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), stem)
        if stem != w.upper():
            out += w[-2] + "s"
        words.append(out)
    return " ".join(words).replace("L.l.c.", "L.L.C.")


def date_words(iso):
    y, m, d = iso.split("-")
    return f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(m)-1]} {int(d)}" + ("" if y == "2026" else f", {y}")


# Florida area codes by the first three digits of the filing's ZIP (generous: neighbours included, since owners'
# cell numbers often come from the next county over)
ZIP3_AREA = {
    "320": {"904", "386", "352"}, "321": {"386", "407", "321", "904"}, "322": {"904", "386"}, "323": {"850"}, "324": {"850"},
    "325": {"850"}, "326": {"352", "386", "904"}, "327": {"407", "689", "321", "386", "352"}, "328": {"407", "689", "321"},
    "329": {"321", "407", "772"}, "330": {"305", "786", "954", "754", "645"}, "331": {"305", "786", "645"},
    "332": {"305", "786", "645"}, "333": {"954", "754", "305", "786"}, "334": {"561", "954", "754", "772"},
    "335": {"813", "656", "727", "863"}, "336": {"813", "656", "727"}, "337": {"727", "813", "656"},
    "338": {"863", "813", "656", "407"}, "339": {"239", "941"}, "340": {"239"}, "341": {"239"},
    "342": {"941", "813", "656", "727"}, "344": {"352", "407"}, "346": {"727", "352", "813", "656"},
    "347": {"407", "689", "321", "352", "863"}, "349": {"772", "561", "321"},
}


def best_officer(offs):
    ppl = [o for o in offs if o.get("type") == "P"]
    for pref in ("Manager", "Managing Member", "President", "CEO", "Owner", "Authorized Member", "Authorized Person"):
        for o in ppl:
            if o["title"] == pref:
                return o
    return ppl[0] if ppl else None


# --- inputs ------------------------------------------------------------------------------------------
filings = {r["doc"]: r for r in json.load(open(os.path.join(SCRATCH, "work", "sunbiz60_all.json")))}
lookups = {}
for p in [os.path.join(W, "sunbiz_maps.jsonl")] + sorted(glob.glob(os.path.join(W, "sunbiz_maps_[sr][0-9].jsonl"))):
    for d in jl(p):
        lookups[d["doc"]] = d
audit = {}
for p in ("audit.jsonl", "audit2.jsonl", "audit3.jsonl"):
    for a in jl(os.path.join(W, p)):
        audit[a["url"]] = a
rendered = {r["url"]: r for r in jl(os.path.join(W, "audit_render.jsonl"))}
# phone -> business names across every Maps listing we collected (shared phone = lead-gen or call center)
phone_names = defaultdict(set)
for rec in jl(os.path.join(W, "maps_paged.jsonl")):
    for b in rec["results"]:
        if b["phone"]:
            phone_names[digits(b["phone"])].add(" ".join(sorted(distinct(b["title"]))) or b["title"].upper())
for d in lookups.values():
    for c in d.get("candidates") or []:
        if c.get("phone"):
            phone_names[digits(c["phone"])].add(" ".join(sorted(distinct(c["title"]))) or c["title"].upper())


def site_state(url):
    """(points, status, gap) for the listing's website, using only evidence we actually have."""
    if not url:
        return 2, "none", "no website on the Google profile"
    if SOCIAL.search(url):
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        return 2, "social/profile page only", f"the profile's website link goes to {host}, not a site they own"
    r = rendered.get(url)
    if r and r.get("status"):
        text = f"{r.get('title', '')} {r.get('text_head', '')}"
        if re.search(r"website expired|account has expired|parked domain|for sale|coming soon|under construction|404\.? not found|site not found", text, re.I):
            return 2, "parked/under construction", "the website is a placeholder or expired page"
        if r["status"] in (404, 410, 500, 502, 504):
            return 2, "broken", f"the website returns an error (HTTP {r['status']})"
        if r.get("words", 0) >= 25 and r["status"] < 400 and not r.get("bot_wall"):
            if not r.get("viewport") or (r.get("overflow_px") or 0) > 20:
                return 2, "loads; fails audit", "the website isn't built for phones"
            if not r.get("tel_link") and not r.get("form") and not r.get("booking") and not r.get("cta_above_fold"):
                return 2, "loads; fails audit", "the website has no tap-to-call, quote form or booking"
            return 0, "loads; passes basic audit", ""
    a = audit.get(url)
    if a and not a.get("loads") and re.search(r"Name or service|No address", a.get("error", "")):
        return 2, "broken", "the website's domain doesn't resolve"
    return None, "not audited", ""  # unknown: never guessed


def live_check(lk_query, data_id, phone):
    """Re-query Google right before writing: listing must still come back with the same phone and not be closed."""
    try:
        params = {"tbm": "map", "authuser": "0", "hl": "en", "q": lk_query,
                  "pb": maps_api.PB.format(lat=28.0, lon=-81.7, zoom=7, offset=0)}
        import urllib.parse, urllib.request
        raw = urllib.request.urlopen(urllib.request.Request("https://maps.google.com/search?" + urllib.parse.urlencode(params),
                                                            headers={"User-Agent": maps_api.UA}), timeout=30).read().decode("utf-8", "replace")
        data = json.loads(raw[raw.index("\n") + 1:])
    except Exception as e:
        return "error", str(e)[:80]
    for it in maps_api.g(data, 0, 1) or []:
        b = maps_api.g(it, 14)
        if isinstance(b, list) and maps_api.g(b, 10) == data_id:
            if CLOSED.search(json.dumps(b)):
                return "closed", ""
            now_phone = (maps_api.g(b, 178, 0, 0) or "")
            if digits(now_phone) != digits(phone):
                return "phone_changed", now_phone
            return "ok", ""
    return "missing", ""


# --- candidates --------------------------------------------------------------------------------------
cands = []
for doc, r in filings.items():
    if r["filed"] < WINDOW_START or r["zip"][:2] not in ("32", "33", "34") or not classify(r["name"]):
        continue
    o = best_officer(r["officers"])
    owner_conf = "high"
    if not o and r.get("ra_type") == "P":
        # many single-member LLCs list no manager, only a registered agent; a person agent is usually the owner
        o = {"title": "Registered Agent", "type": "P", "display": r["ra_display"], "name": r["registered_agent"]}
        owner_conf = "medium"
    if not o:
        stats["drop_no_person_owner"] += 1
        continue
    lk = lookups.get(doc)
    if not lk:
        stats["drop_not_looked_up"] += 1
        continue
    stats["filings_checked"] += 1
    da = distinct(r["name"])
    full = lk.get("match") or {}
    m = None
    for c in lk.get("candidates") or []:
        db = distinct(c.get("title", ""))
        same_city = norm_city(c.get("city")) == norm_city(r["city"])
        if da and da == db and (same_city or lk.get("n_results") == 1):
            m = {**c, **(full if full.get("title") == c.get("title") else {})}
            m["_same_city"] = same_city
            break
    if not m:
        stats["drop_no_exact_google_listing"] += 1
        continue
    if not (classify(m.get("title", "")) or classify(" ".join(m.get("categories") or []))):
        stats["drop_listing_not_trade"] += 1
        continue
    ph = digits(m.get("phone"))
    if len(ph) != 10 or ph[:3] not in FL_AREA:
        stats["drop_no_florida_phone"] += 1
        continue
    if not m["_same_city"] and ph[:3] not in ZIP3_AREA.get(r["zip"][:3], FL_AREA):
        stats["drop_phone_area_code_outside_filing_region"] += 1
        continue
    if len(phone_names[ph]) > 1:
        stats["drop_phone_shared_with_other_business"] += 1
        continue
    rv = m.get("reviews") or 0
    if rv > 25:
        stats["drop_established_business_refiled"] += 1
        continue
    pts, status, gap = site_state(m.get("website", ""))
    if pts is None:
        cands.append(("needs_audit", r, m, o, None, None, None, owner_conf))
        continue
    if pts == 0:
        stats["drop_has_working_website"] += 1
        continue
    bare = rv == 0 or not m.get("has_hours", True)
    if not bare:
        stats["drop_profile_not_bare"] += 1
        continue
    cands.append(("ok", r, m, o, status, gap, rv, owner_conf))

need = sorted({c[2]["website"] for c in cands if c[0] == "needs_audit"})
open(os.path.join(W, "eclat_needs_audit.txt"), "w").write("\n".join(need))
stats["drop_site_not_audited_yet"] = len(need)
ok = [c for c in cands if c[0] == "ok"]
print(f"{len(ok)} candidates before the live re-check; {len(need)} sites still need a rendered audit", flush=True)

# --- live re-check and write -------------------------------------------------------------------------
os.makedirs(os.path.join(ROOT, "leads"), exist_ok=True)
out_path = os.path.join(ROOT, "leads", "eclat_leads.csv")
rows = []
with open(out_path, "w", newline="") as fh:
    w = csv.DictWriter(fh, COLS); w.writeheader(); fh.flush()
    for _, r, m, o, status, gap, rv, owner_conf in sorted(ok, key=lambda c: c[1]["filed"], reverse=True):
        q = f"{m['title']} {r['city'].title()} FL"
        state, extra = live_check(q, m.get("data_id", ""), m["phone"]) if m.get("data_id") else ("missing", "")
        if state == "missing" and m.get("data_id"):
            state, extra = live_check(m["title"] + " FL", m["data_id"], m["phone"])
        maps_api.pause(1.5, 3)
        if state != "ok":
            stats[f"drop_live_check_{state}"] += 1
            continue
        owner = o.get("display") or o["name"].title()
        when = date_words(r["filed"])
        registered = f"registered {nice(r['name'])} on {when}" if r["filed"] <= "2026-10-28" else f"filed {nice(r['name'])} with the state to start {when}"
        no_reviews = rv == 0
        gaps = [gap] + (["Google profile has 0 reviews"] if no_reviews else []) + (["no business hours on the Google profile"] if not m.get("has_hours", True) else [])
        bare_phrase = "no reviews yet" if no_reviews else "no hours listed"
        if status == "none":
            hook = (f"You {registered} and your Google profile is already live at {fmt_phone(m['phone'])}, "
                    f"but there's no website behind it and {bare_phrase}, so people comparing {TRADE_PLURAL[r['trade']]} "
                    f"in {r['city'].title()} have nothing to check before they call.")
        else:
            hook = (f"You {registered} and your Google profile is live at {fmt_phone(m['phone'])}, "
                    f"but {SITE_PHRASE[status]} and the profile has {bare_phrase}.")
        row = {
            "lead_id": f"EC-{len(rows) + 1:04d}", "source": "sunbiz+google_maps",
            "source_url": f"https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults?inquiryType=DocumentNumber&searchTerm={r['doc']}",
            "trigger_signal": "new LLC/corp filing" if r["kind"] == "corp" else "new fictitious name (DBA) filed by the owner",
            "trigger_date": r["filed"], "business_name": nice(r["name"]), "trade": r["trade"], "city": r["city"].title(), "state": "FL",
            "owner_name": owner, "owner_name_confidence": owner_conf, "owner_phone": "", "business_phone": fmt_phone(m["phone"]),
            "phone_confidence": "high", "email": "", "website": m.get("website", ""), "website_status": status,
            "google_maps_url": m.get("maps_url", ""), "facebook": m["website"] if "facebook.com" in (m.get("website") or "") else "",
            "instagram": m["website"] if "instagram.com" in (m.get("website") or "") else "", "linkedin": "",
            "gap_findings": "; ".join(gaps), "score": 7,
            "primary_pitch": "new website" if status in ("none", "social/profile page only") else "website rebuild",
            "pitch_hook": hook,
            "notes": "; ".join([
                f"Google listing: {m['title']} ({rv} reviews)",
                f"owner = {o['title']} on the state filing" + (" (no manager listed; a person registered agent is usually the owner of a single-member LLC)" if owner_conf == "medium" else ""),
                "phone verified: exact name match" + (" in the same city" if m.get("_same_city") else " (Google returned only this business)")
                + ", Florida area code, not used by any other business in our data, re-checked live today",
                "listing not marked closed on Google (checked today)",
                "social activity and review dates NOT verified (Facebook/Instagram/Google reviews need a login from this environment)",
                f"principal address {r['addr']}, {r['city'].title()} {r['zip']}",
            ]),
        }
        w.writerow(row); fh.flush()
        rows.append(row)

stats["final"] = len(rows)
stats["by_trade"] = dict(Counter(r["trade"] for r in rows))
stats["by_status"] = dict(Counter(r["website_status"] for r in rows))
json.dump(stats, open(os.path.join(W, "eclat_stats.json"), "w"), indent=1)
print(json.dumps(stats, indent=1))
