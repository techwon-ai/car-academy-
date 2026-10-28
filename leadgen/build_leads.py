#!/usr/bin/env python3
"""Merge every source into scored, pitched leads.

Reads the working files under output/work and output/maps, writes:
  leads/techwon_leads.csv  (appended row by row as each lead is finalised)
  leads/top50.csv
  output/work/build_stats.json (numbers the run log quotes)

Scoring follows the brief exactly:
  +3 intent trigger (new filing, new domain, hiring a marketer, publicly asked for help)
  +2 no website, or broken/parked website
  +2 site fails mobile, very slow, or no CTA
  +1 no or incomplete Google Business Profile
  +1 30+ reviews
  +1 owner name AND a phone
  -3 franchise/chain or already has an agency
Two judgment calls are flagged in notes and the run log: suspected lead-gen listings are scored as
"already has an agency" (-3), and listings with 500+ reviews are dropped as too big for a 1-20 person ICP.
"""
import csv, glob, json, os, re, sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "leadgen"))
sys.path.insert(0, os.path.join(ROOT, "leadgen", "enrich"))
from trades import classify
from sunbiz_maps_lookup import similarity, toks, GENERIC

W = os.path.join(ROOT, "output", "work")
SCRATCH = sys.argv[1] if len(sys.argv) > 1 else ""
COLS = ["lead_id", "source", "source_url", "trigger_signal", "trigger_date", "business_name", "trade", "city", "state",
        "owner_name", "owner_name_confidence", "owner_phone", "business_phone", "phone_confidence", "email", "website",
        "website_status", "google_maps_url", "facebook", "instagram", "linkedin", "gap_findings", "score",
        "primary_pitch", "pitch_hook", "notes"]
TRADE_LABEL = {"roofing": "roofing", "hvac": "HVAC", "plumbing": "plumbing", "landscaping": "landscaping/lawn care",
               "cleaning": "cleaning", "pressure_washing": "pressure washing", "pest_control": "pest control",
               "pool_service": "pool service"}
FL_AREA = {"239", "305", "321", "324", "352", "386", "407", "448", "561", "645", "656", "689", "727", "728", "754", "772",
           "786", "813", "850", "863", "904", "941", "954"}
TRADE_WHO = {"roofing": "a roofer", "hvac": "an HVAC company", "plumbing": "a plumber", "landscaping": "a lawn care company",
             "cleaning": "a cleaning company", "pressure_washing": "a pressure washing company", "pest_control": "a pest control company",
             "pool_service": "a pool service"}
QUERY_TRADE = {"roof": "roofing", "ac ": "hvac", "hvac": "hvac", "air cond": "hvac", "plumb": "plumbing", "drain": "plumbing",
               "lawn": "landscaping", "landscap": "landscaping", "clean": "cleaning", "maid": "cleaning",
               "pressure": "pressure_washing", "soft wash": "pressure_washing", "pest": "pest_control",
               "extermin": "pest_control", "pool": "pool_service"}
CHAIN = re.compile(r"roto-?rooter|mr\.? rooter|benjamin franklin|one hour|aire serv|trugreen|terminix|orkin|rentokil|truly nolen|"
                   r"arrow exterminators|massey|molly maid|merry maids|the maids|maidpro|two maids|jan-?pro|coverall|servicemaster|"
                   r"servpro|stanley steemer|chem-?dry|heaven'?s best|zerorez|window genie|weed man|lawn doctor|spring-?green|"
                   r"mosquito joe|mosquito squad|mosquitonix|mosquito authority|abc home|poolwerx|america'?s swimming pool|"
                   r"frank gay|service experts|ars\b|rescue rooter|horizon services|cooltoday|cool today|del-?air|"
                   r"hulett|nozzle nolen|florida pest control|turner pest|home depot|lowe'?s|sears|davey tree|bartlett tree|"
                   r"brightview|yellowstone landscape|sunbelt|pool scouts|pool troopers|blue science|aqua plumbing & air|"
                   r"jenkins|cousins air|fuse hvac|ferran|bay area air|lion home|tiger home|hoffmann|baker brothers|"
                   r"modernize|homeadvisor|angi\b|neighborly|ecoshield|aptive|hometeam pest|sentricon|mosquito shield|"
                   r"busy bee|glass doctor|men in kilts|dryer vent wizard|sir grout|fish window|shack shine|"
                   r"tru-?green|lawn love|greenpro|mighty dog|1-800|1 800|crown roofing|advanced roofing|tampa bay trane|"
                   r"carrier|trane|lennox|rheem|goodman|budget heating|all american|fischer", re.I)

# --- inputs --------------------------------------------------------------------------------------
def jl(path):
    return [json.loads(l) for l in open(path) if l.strip()] if os.path.exists(path) else []


audit = {}
for p in ("audit.jsonl", "audit2.jsonl", "nrd_audit.jsonl"):
    for a in jl(os.path.join(W, p)):
        audit[a["url"]] = {**audit.get(a["url"], {}), **a}
rendered = {}
for p in ("audit_render.jsonl",):
    for a in jl(os.path.join(W, p)):
        rendered[a["url"]] = a
NOT_AGENCY = re.compile(r"^(wordpress|wix|godaddy|squarespace|weebly|shopify|duda|google|elementor|divi|hostinger|jimdo|webflow|yelp|this|complianz|cookie|webador|the|our|us|you)\b", re.I)
dbpr = json.load(open(os.path.join(W, "dbpr_idx.json")))
quarterly = json.load(open(os.path.join(W, "sunbiz_quarterly_trades.json"))) if os.path.exists(os.path.join(W, "sunbiz_quarterly_trades.json")) else []


def norm_name(s):
    s = re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper().replace("&", " AND "))
    s = re.sub(r"\b(LLC|L L C|INC|CORP|CORPORATION|CO|COMPANY|THE|OF|PA|DBA)\b", " ", s)
    return " ".join(s.split())


q_by_name = defaultdict(list)
for e in quarterly:
    q_by_name[norm_name(e["name"])].append(e)


def norm_city(c):
    c = (c or "").upper().replace("SAINT ", "ST ").replace("ST. ", "ST ").replace("FT. ", "FORT ").replace("FT ", "FORT ")
    return re.sub(r"[^A-Z ]", "", c).strip()


def person(o):
    """Display name parsed from Sunbiz's fixed-width last/first/middle fields (keeps double surnames intact)."""
    return o.get("display") or o["name"].title()


def best_officer(offs):
    ppl = [o for o in offs if o.get("type") == "P"]
    for pref in ("Manager", "Managing Member", "President", "CEO", "Authorized Member", "Owner", "Authorized Person"):
        for o in ppl:
            if o["title"] == pref:
                return o
    return ppl[0] if ppl else None


def cid_url(data_id):
    if data_id and ":" in data_id:
        try:
            return f"https://maps.google.com/?cid={int(data_id.split(':')[1], 16)}"
        except ValueError:
            pass
    return ""


def nice(name):
    """'CLEAN & LUMINOUS LLC' -> 'Clean & Luminous LLC'; keeps suffixes and vowel-less initialisms (JB, A&A) upper-case."""
    words = []
    for w in name.split():
        core = re.sub(r"[^A-Za-z]", "", w).upper()
        if core in {"LLC", "LLP", "HVAC", "AC", "USA", "FL", "II", "III", "PA"} or (core and len(core) <= 3 and not re.search(r"[AEIOUY]", core)) or re.fullmatch(r"[A-Z]&[A-Z]", w):
            words.append(w.upper())
        elif core == "INC":
            words.append("Inc.")
        else:
            words.append(re.sub(r"'S\b", "'s", re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), w)))
    return " ".join(words).replace("L.l.c.", "L.L.C.")


def fmt_phone(p):
    d = re.sub(r"\D", "", p or "")
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else (p or "")


def strip_utm(u):
    return re.sub(r"[?&](utm_[^&]+|gclid=[^&]+|y_source=[^&]+)", "", u or "").rstrip("?&")


def domain(u):
    m = re.match(r"(?:https?://)?(?:www\.)?([^/:?#]+)", (u or "").lower())
    return m.group(1) if m else ""


def date_words(iso):
    try:
        y, m, d = iso.split("-")
        return f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(m)-1]} {int(d)}" + ("" if y == "2026" else f", {y}")
    except Exception:
        return iso


def BROKEN_PHRASE(gap):
    if "does not resolve" in gap:
        return "doesn't load at all (the domain doesn't resolve)"
    if "certificate" in gap:
        return "throws a browser security warning before it loads"
    if "placeholder" in gap or "parked" in gap:
        return "is still a placeholder page"
    return gap.replace("site returns", "returns")


def FAIL_PHRASE(gaps):
    g = gaps[0] if gaps else ""
    if "viewport" in g:
        return "shows a shrunken desktop page (no mobile viewport tag)"
    if "wider than" in g:
        return "spills off the screen sideways (" + g.split(": ")[1] + ")"
    if "fully load" in g:
        return g
    if "tap-to-call" in g:
        return "has no tap-to-call link, quote form, or booking button on the homepage"
    return g or "fails a basic mobile check"


# --- website assessment ----------------------------------------------------------------------------
def site_check(url):
    """Return (status, gaps[list], plus2_broken, plus2_fail, agency, facts dict)."""
    if not url:
        return "none", ["no website"], True, False, "", {}
    if re.search(r"facebook\.com|instagram\.com|linktr\.ee|yelp\.com|nextdoor|business\.site|thumbtack|angi\.com|homeadvisor|docs\.google\.com|forms\.gle", url, re.I):
        return "social/profile page only", [f"website field points to a {domain(url)} page, not an owned site"], True, False, "", {}
    r = rendered.get(url) or rendered.get(strip_utm(url))
    a = audit.get(url) or audit.get(strip_utm(url))
    if r and not r.get("error") and r.get("status"):
        return render_check(r, a or {})
    if not a:
        return "not audited", [], False, False, "", {}
    if not a.get("loads"):
        err = a.get("error", "")
        if re.search(r"Name or service|No address|HTTP 404|HTTP 410|HTTP 50[024]|CERTIFICATE|Tunnel connection failed", err):
            why = ("domain does not resolve" if "Name or service" in err or "No address" in err else
                   "browser security-certificate warning" if "CERTIFICATE" in err else
                   f"site returns an error page ({err})" if err.startswith("HTTP") else "site unreachable")
            return "broken", [why], True, False, "", {}
        return "could not audit (blocks automated requests)", [], False, False, "", {}
    # Without a rendered check, a thin or failing static page may just be a JS site or a bot wall,
    # so the quick HTML audit alone never earns "parked" or "fails audit" points.
    return "loads (quick check only)", [], False, False, a.get("agency_credit", ""), a


BOT_TEXT = re.compile(r"checking your browser|security checkpoint|verify your browser|verify you are human|just a moment|"
                      r"403 forbidden|access (to this resource )?(is )?denied|too many requests|attention required", re.I)
DEAD_TEXT = re.compile(r"website expired|account has expired|parked domain|domain (is|may be) for sale|buy this domain|"
                       r"coming soon|under construction|launching soon|en construcci|pr[oó]ximamente|404\.? (that.s an error|not found)|"
                       r"not found no site here|site not found|this site can.t be reached|account (has been )?suspended|"
                       r"default web site page|welcome to nginx|future home of", re.I)


def render_check(r, a):
    text = f"{r.get('title', '')} {r.get('text_head', '')}"
    if r.get("bot_wall") or BOT_TEXT.search(text):
        return "could not audit (bot protection page)", [], False, False, "", {}
    if "upstream request failed" in text or not r.get("words"):
        # the proxy could not reach the host, or nothing rendered: only call it broken if DNS also failed
        if a and re.search(r"Name or service|No address", a.get("error", "")):
            return "broken", ["domain does not resolve"], True, False, "", {}
        return "could not audit (no response through our network)", [], False, False, "", {}
    if DEAD_TEXT.search(text):
        return "parked/under construction", [f"website shows a placeholder or dead page: \"{r.get('text_head', '')[:70].strip()}\""], True, False, "", r
    if r["status"] in (401, 403, 429, 503):  # usually bot blocking or rate limiting aimed at headless browsers
        return f"could not audit (HTTP {r['status']} to automated browser)", [], False, False, "", {}
    if r["status"] >= 400:
        return "broken", [f"site returns an error page (HTTP {r['status']})"], True, False, "", {}
    if (r.get("words") or 0) < 25:
        return "could not audit (page rendered almost no text)", [], False, False, "", {}
    gaps, fail = [], False
    if not r.get("viewport") or (r.get("overflow_px") or 0) > 20:
        gaps.append("not mobile-friendly: " + ("no viewport tag" if not r.get("viewport") else f"page is {r['overflow_px']}px wider than a phone screen")); fail = True
    if (r.get("load_s") or 0) > 15:  # timings run through this container's proxy, so the bar is set high
        gaps.append(f"took {r['load_s']:.0f}s to fully load in our mobile test"); fail = True
    if not r.get("tel_link") and not r.get("form") and not r.get("booking") and not r.get("cta_above_fold"):
        gaps.append("no tap-to-call link, quote form, booking, or call/quote button on the homepage"); fail = True
    elif not r.get("tel_above_fold") and not r.get("cta_above_fold"):
        gaps.append("no call or quote button on the first phone screen")
    if not r.get("https"):
        gaps.append("no HTTPS (browsers show 'Not secure')")
    if r.get("copyright_year") and r["copyright_year"] <= 2021:
        gaps.append(f"footer copyright {r['copyright_year']}")
    ag = r.get("agency_credit", "")
    if ag and NOT_AGENCY.match(ag):
        ag = ""
    status = "loads; fails audit" if fail else "loads; passes basic audit"
    return status, gaps, False, fail, ag, {**a, **r}


# --- leads -----------------------------------------------------------------------------------------
leads = []


def add(**k):
    leads.append(k)


# 1) Google Maps -----------------------------------------------------------------------------------
maps = {}
for rec in jl(os.path.join(W, "maps_paged.jsonl")):
    qt = next((v for k, v in QUERY_TRADE.items() if k in rec["query"].lower()), None)
    for b in rec["results"]:
        key = b["data_id"] or b["title"] + b["phone"]
        if key not in maps:
            maps[key] = {**b, "search_city": rec["city"], "q_trade": qt, "query": rec["query"]}
for path in glob.glob(os.path.join(ROOT, "output", "maps", "*.csv")):
    city = os.path.basename(path)[:-4].replace("_", " ").title().replace("St Petersburg", "St. Petersburg")
    for r in csv.DictReader(open(path)):
        key = r["data_id"] or r["title"] + r["phone"]
        if key in maps:
            continue
        try:
            reviews = int(r["review_count"] or 0)
        except ValueError:
            reviews = 0
        maps[key] = {"title": r["title"], "categories": [r["category"]] if r["category"] else [], "website": r["website"],
                     "phone": r["phone"], "rating": r["review_rating"], "reviews": reviews, "address": r["address"],
                     "city": (r.get("complete_address") and json.loads(r["complete_address"]).get("city")) or "",
                     "data_id": r["data_id"], "place_id": r["place_id"], "has_hours": bool(r["open_hours"] and r["open_hours"] != "{}"),
                     "description": r["descriptions"], "search_city": city, "q_trade": classify(r["title"]), "query": ""}
title_addrs = defaultdict(set)
phone_titles = defaultdict(set)
for b in maps.values():
    title_addrs[norm_name(b["title"])].add(b["address"][:25])
    if b["phone"]:
        phone_titles[re.sub(r"\D", "", b["phone"])].add(norm_name(b["title"]))
stats = Counter()
stats["maps_unique"] = len(maps)
for b in maps.values():
    trade = classify(b["title"]) or b["q_trade"]
    if not trade:
        continue
    reviews = b["reviews"] or 0
    web = strip_utm(b["website"])
    status, gaps, broken, fail, agency, facts = site_check(web)
    qualifies = (not web and reviews >= 30) or (bool(web) and (broken or fail))  # the brief: no site needs 30+ reviews
    if not qualifies:
        continue
    stats["maps_qualified_raw"] += 1
    notes, minus = [], 0
    chain = CHAIN.search(b["title"]) or len(title_addrs[norm_name(b["title"])]) >= 3
    if chain:
        minus = 3; notes.append("franchise/chain or multi-location brand (-3)")
    if agency:
        minus = 3; notes.append(f"footer credits an agency: {agency} (-3)")
    addr_suite = re.search(r"(?:\b(?:ste|suite|unit|apt)\b|#)\s*\.?\s*\d{3,}", b["address"], re.I)
    shared_phone = len(phone_titles[re.sub(r"\D", "", b["phone"])]) >= 2
    if not web and (addr_suite or shared_phone) and reviews >= 30:
        minus = 3; notes.append("possible lead-gen/virtual-office listing: " + ("suite/mailbox-style address" if addr_suite else "phone shared with another listing") + " (-3, verify before calling)")
    if reviews >= 500:
        stats["maps_dropped_too_big"] += 1
        continue
    city = b["city"] or b["search_city"]
    # owner name: Sunbiz quarterly officers (exact normalized name + same city) then DBPR licensee
    owner, oconf, osrc = "", "", ""
    nn = norm_name(b["title"])
    for e in q_by_name.get(nn, []):
        if norm_city(e["city"]) == norm_city(city):
            o = best_officer(e["officers"])
            if o:
                owner, oconf, osrc = person(o), "high", f"Sunbiz officer ({o['title']}) of {e['name']}"
                break
    if not owner:
        for e in dbpr.get(nn, []):
            if norm_city(e["city"]) == norm_city(city):
                owner, oconf, osrc = e["name"], "medium", f"DBPR license holder {e['license']} (qualifier, may not be owner)"
                break
    gbp_incomplete = (not b.get("has_hours")) or reviews == 0
    sc = 0
    gap = list(gaps)
    if broken:
        sc += 2
    if fail:
        sc += 2
    if gbp_incomplete:
        sc += 1; gap.append("Google profile incomplete: " + ("no hours listed" if not b.get("has_hours") else "0 reviews"))
    if reviews >= 30:
        sc += 1
    if owner and b["phone"]:
        sc += 1
    sc -= minus
    if status.startswith("social"):
        pitch = "new website"
        hook = f"Your Google profile's website link goes to a {domain(web)} page, so {reviews} reviews' worth of searchers land somewhere you can't take a quote request."
    elif not web:
        pitch = "new website"
        hook = (f"{reviews} Google reviews at {b['rating']}★ and no website on your profile, so people who want to see your work before calling end up on a competitor's site."
                if reviews >= 30 else f"Your Google profile has no website link, so anyone comparing {TRADE_LABEL[trade]} options in {city} has nothing to click through to.")
    elif broken:
        pitch = "website rebuild"
        hook = f"The website linked from your Google profile, {domain(web)}, {BROKEN_PHRASE(gaps[0])}, so people coming from your {reviews} reviews hit a dead end."
    else:
        pitch = "website rebuild"
        hook = f"On a phone, {domain(web)} {FAIL_PHRASE(gaps)}" + (f", and {reviews} Google reviews are sending people to it." if reviews >= 30 else ".")
    add(source="google_maps", source_url=cid_url(b["data_id"]) or b.get("maps_url", ""), trigger_signal="established listing with website gap",
        trigger_date="", business_name=b["title"], trade=trade, city=city, state="FL", owner_name=owner,
        owner_name_confidence=oconf, owner_phone="", business_phone=fmt_phone(b["phone"]), phone_confidence="high" if b["phone"] else "",
        email=", ".join(facts.get("emails", [])), website=web, website_status=status, google_maps_url=cid_url(b["data_id"]) or b.get("maps_url", ""),
        facebook=facts.get("facebook", "") or (web if "facebook.com" in web else ""), instagram=facts.get("instagram", ""), linkedin=facts.get("linkedin", ""),
        gap_findings="; ".join(gap), score=sc, primary_pitch=pitch, pitch_hook=hook,
        notes="; ".join(notes + ([f"owner via {osrc}"] if osrc else []) + [f"{reviews} reviews, rating {b['rating']}"]),
        _phone=b["phone"], _domain=domain(web), _reviews=reviews)

def distinct_tokens(name):
    return set(toks(re.sub(r",?\s*(L\.?L\.?C\.?|INC\.?|CORP\.?)\s*$", "", name or "", flags=re.I))) - GENERIC


def pick_listing(r, lk):
    """Choose the Maps listing for a filing only on a strict name match.
    The distinctive words (trade words removed) must be identical, and either there are 2+ of them, the city
    matches, or Google returned that single business. Then the listing needs a Florida phone or the same city,
    and must itself be a trade business."""
    cands = lk.get("candidates") or ([lk["match"]] if lk.get("match") else [])
    full = lk.get("match") or {}
    da = distinct_tokens(r["name"])
    for c in cands:
        db = distinct_tokens(c.get("title", ""))
        same_city = norm_city(c.get("city")) == norm_city(r["city"])
        if not da or da != db or not (len(da) >= 2 or same_city or lk.get("n_results") == 1):
            continue
        fl = re.sub(r"\D", "", c.get("phone", ""))[-10:-7] in FL_AREA
        if not (fl or same_city):
            stats["sunbiz_match_rejected_out_of_state"] += 1
            return None
        cats = full.get("categories", []) if full.get("title") == c.get("title") else []
        if not (classify(c.get("title", "")) or classify(" ".join(cats))):
            stats["sunbiz_match_rejected_not_trade"] += 1
            return None
        return {**c, **(full if full.get("title") == c.get("title") else {})}
    return None


# 2) Sunbiz new filings ----------------------------------------------------------------------------
sunbiz = {r["doc"]: r for r in json.load(open(os.path.join(SCRATCH, "work", "sunbiz_fl.json")))} if SCRATCH else {}
look = {d["doc"]: d for d in jl(os.path.join(W, "sunbiz_maps.jsonl"))}
verify = {d["key"]: d for d in jl(os.path.join(W, "sunbiz_web_verify.jsonl"))}
for doc, r in sunbiz.items():
    lk = look.get(doc)
    if not lk:
        continue
    if not classify(r["name"]):  # classifier was tightened after parsing (e.g. "garage clean outs")
        stats["sunbiz_dropped_not_icp"] += 1
        continue
    if r["filed"] < "2026-09-01":  # daily files also carry older entities with new activity
        stats["sunbiz_dropped_not_new"] += 1
        continue
    stats["sunbiz_looked_up"] += 1
    m = pick_listing(r, lk)
    o = best_officer(r["officers"])
    owner = person(o) if o else ""
    filed = r["filed"]
    kind = "LLC/corp filing" if r["kind"] == "corp" else "fictitious name (DBA) registration"
    # an existing company filing a DBA is not a new business; an entity officer means a holding company
    if r["kind"] == "fictitious" and not any(x.get("type") == "P" for x in r["officers"]):
        stats["sunbiz_dropped_corporate_dba"] += 1
        continue
    sc, gap, notes = 3, [], []
    phone, web, status, maps_url, facts = "", "", "", "", {}
    v = verify.get(doc)
    if m:
        phone, web, maps_url = m.get("phone", ""), strip_utm(m.get("website", "")), m.get("maps_url", "")
        status, sgaps, broken, fail, agency, facts = site_check(web)
        if broken:
            sc += 2
        if fail:
            sc += 2
        gap += sgaps
        if (m.get("reviews") or 0) == 0:
            sc += 1; gap.append("Google profile exists but has 0 reviews")
        if (m.get("reviews") or 0) >= 30:
            sc += 1
        if agency:
            sc -= 3; notes.append(f"footer credits an agency: {agency} (-3)")
        if CHAIN.search(m.get("title", "")):
            sc -= 3; notes.append("matches a franchise/chain brand (-3)")
        notes.append(f"Maps listing: {m.get('title')} ({m.get('reviews', 0)} reviews)")
    else:
        sc += 1; gap.append("no Google Business Profile found under this name")
        if v and v.get("verdict") == "no_site_found":
            sc += 2; status = "none found"; gap.append("no website found in web search")
        elif v and v.get("verdict") == "site_found":
            web = v.get("url", ""); status = "found via web search (not audited)"
            notes.append("website found via search: " + web)
        else:
            status = "none on Google Maps; web search not completed (engines blocked)"
    if owner and phone:
        sc += 1
    if CHAIN.search(r["name"]):
        sc -= 3; notes.append("name matches a franchise/chain brand (-3)")
    rv = (m or {}).get("reviews") or 0
    when = date_words(filed)
    if not m:
        pitch = "Google Business Profile setup/optimization"
        hook = f"You registered {nice(r['name'])} with the state on {when}, and there's no Google Business Profile under that name yet, which is the first thing people check before hiring {TRADE_WHO[r['trade']]}."
    elif not web:
        pitch = "new website"
        hook = (f"You just registered {nice(r['name'])} ({when}), and your {m.get('title')} listing already has {rv} reviews, but no website behind it to turn that reputation into quote requests."
                if rv >= 10 else f"You registered {nice(r['name'])} on {when} and {m.get('title')} is already on Google Maps, but there's no website behind the listing yet.")
    elif status.startswith("social"):
        pitch = "new website"
        hook = f"You registered {nice(r['name'])} on {when}, and the website link on your Google listing goes to a {domain(web)} page instead of a site you own."
    elif status == "broken" or status.startswith("parked"):
        pitch = "website rebuild"
        hook = f"You registered {nice(r['name'])} on {when}, but the website on your Google listing, {domain(web)}, {BROKEN_PHRASE(gap[0]) if gap else 'is not working'}."
    elif "fails" in status:
        pitch = "website rebuild"
        hook = f"You registered {nice(r['name'])} on {when}; on a phone, {domain(web)} {FAIL_PHRASE(sgaps)}."
    else:
        pitch = "local SEO"
        hook = f"You registered {nice(r['name'])} on {when} and the listing and site are live; the next job is ranking in the {r['city'].title()} map pack for {TRADE_LABEL[r['trade']]} searches."
    src_url = f"https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults?inquiryType=DocumentNumber&searchTerm={doc}"
    add(source="sunbiz", source_url=src_url, trigger_signal=f"new {kind} ({r['filing_type']})", trigger_date=filed,
        business_name=r["name"], trade=r["trade"], city=r["city"].title(), state="FL", owner_name=owner,
        owner_name_confidence="high" if owner else "", owner_phone="", business_phone=fmt_phone(phone),
        phone_confidence="medium" if phone else "", email=", ".join(facts.get("emails", [])), website=web,
        website_status=status, google_maps_url=maps_url, facebook=facts.get("facebook", ""), instagram=facts.get("instagram", ""),
        linkedin=facts.get("linkedin", ""), gap_findings="; ".join(gap), score=sc, primary_pitch=pitch, pitch_hook=hook,
        notes="; ".join(notes + ([f"officer title: {o['title']}"] if o else []) + [f"principal address {r['addr']}, {r['city'].title()} {r['zip']}"]
                        + (["phone is from the matched Google listing (name match, not confirmed by the owner)"] if phone else [])),
        _phone=phone, _domain=domain(web), _reviews=(m or {}).get("reviews", 0))

# 3) Curated small sources (domains, jobs, owner requests) -----------------------------------------
for path in glob.glob(os.path.join(ROOT, "leadgen", "sources", "curated_*.json")):
    for c in json.load(open(path)):
        web = c.get("website", "")
        a = audit.get(web) or audit.get("https://" + domain(web)) or audit.get(domain(web)) or {}
        row = {k: c.get(k, "") for k in COLS if k not in ("lead_id",)}
        row.update(_phone=c.get("business_phone", ""), _domain=domain(web), _reviews=c.get("_reviews", 0))
        if "score_base" in c:
            # the curated file records the non-website points; the website part comes from the same audit as everyone else
            sc, gaps = c["score_base"], list(c.get("gaps", []))
            if c.get("website_override"):
                status, wg, broken, fail = c["website_override"], [c["website_gap"]], True, False
            else:
                status, wg, broken, fail, agency, facts = site_check(web)
                if agency:
                    sc -= 3; row["notes"] += f"; footer credits an agency: {agency} (-3)"
            sc += (2 if broken else 0) + (2 if fail else 0)
            row.update(score=sc, website_status=status, gap_findings="; ".join(wg + gaps))
        row["email"] = row["email"] or ", ".join(a.get("emails", []))
        for s in ("facebook", "instagram", "linkedin"):
            row[s] = row[s] or a.get(s, "")
        add(**row)

# --- dedupe across sources (normalized name + phone + domain) ---------------------------------------
def keys(l):
    k = {("n", norm_name(l["business_name"]) + "|" + norm_city(l["city"]))}
    if l["_phone"]:
        k.add(("p", re.sub(r"\D", "", l["_phone"])[-10:]))
    if l["_domain"] and not re.search(r"facebook|instagram|google|yelp", l["_domain"]):
        k.add(("d", l["_domain"]))
    return k


merged, index = [], {}
for l in sorted(leads, key=lambda x: -x["score"]):
    hit = next((index[k] for k in keys(l) if k in index), None)
    if hit is not None:
        m = merged[hit]
        srcs = set(m["source"].split("+")) | {l["source"]}
        m["source"] = "+".join(sorted(srcs))
        for f in ("owner_name", "owner_name_confidence", "business_phone", "phone_confidence", "email", "website", "facebook", "instagram", "linkedin", "trigger_date"):
            if not m[f] and l[f]:
                m[f] = l[f]
        m["notes"] += f"; also found via {l['source']} ({l['trigger_signal']})"
        stats["dedup_merges"] += 1
    else:
        index.update({k: len(merged) for k in keys(l)})
        merged.append(dict(l))
    for k in keys(l):
        index.setdefault(k, hit if hit is not None else len(merged) - 1)

# sites still lacking a rendered audit (fed back to enrich/audit_render.js on the next pass)
with open(os.path.join(W, "unrendered.txt"), "w") as fh:
    fh.write("\n".join(sorted({l["website"] for l in leads if l["website"] and l["website_status"] in ("loads (quick check only)", "not audited")})))
for l in merged:
    for tag, key in (("lead-gen", "flag_leadgen"), ("agency", "flag_agency"), ("chain", "flag_chain")):
        if tag in l["notes"]:
            stats[key] += 1
kept = [l for l in merged if l["score"] >= 4]
stats["below_4_dropped"] = len(merged) - len(kept)
by_src = defaultdict(list)
for l in kept:
    by_src[l["source"].split("+")[0]].append(l)
for v in by_src.values():
    v.sort(key=lambda x: (-x["score"], -x["_reviews"]))

# quota allocation: small sources first (all qualifying), then Maps / Sunbiz split the remainder
final = []
for s in ("new_domain", "job_posting", "owner_request"):
    final += by_src.get(s, [])
remaining = 300 - len(final)
maps_pool, sb_pool = by_src.get("google_maps", []), by_src.get("sunbiz", [])
maps_take = min(len(maps_pool), max(remaining // 2, remaining - len(sb_pool)))
final += maps_pool[:maps_take] + sb_pool[:remaining - maps_take]
final.sort(key=lambda x: (-x["score"], x["source"], -x["_reviews"]))

os.makedirs(os.path.join(ROOT, "leads"), exist_ok=True)
out = os.path.join(ROOT, "leads", "techwon_leads.csv")
with open(out, "w", newline="") as fh:
    w = csv.DictWriter(fh, COLS, extrasaction="ignore"); w.writeheader(); fh.flush()
    for i, l in enumerate(final, 1):
        l["lead_id"] = f"TW-{i:04d}"
        w.writerow(l); fh.flush()
with open(os.path.join(ROOT, "leads", "top50.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, COLS, extrasaction="ignore"); w.writeheader()
    for l in final[:50]:
        w.writerow(l)
stats["final"] = len(final)
stats["pool_by_source"] = {k: len(v) for k, v in by_src.items()}
stats["final_by_source"] = dict(Counter(l["source"] for l in final))
stats["score_dist"] = dict(sorted(Counter(l["score"] for l in final).items(), reverse=True))
stats["with_owner"] = sum(1 for l in final if l["owner_name"])
stats["with_phone"] = sum(1 for l in final if l["business_phone"] or l["owner_phone"])
stats["with_email"] = sum(1 for l in final if l["email"])
stats["trade_dist"] = dict(Counter(l["trade"] for l in final))
json.dump(stats, open(os.path.join(W, "build_stats.json"), "w"), indent=1, default=str)
print(json.dumps(stats, indent=1, default=str))
