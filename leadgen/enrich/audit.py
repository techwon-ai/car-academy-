#!/usr/bin/env python3
"""Quick website audit: loads?, HTTPS, viewport (mobile), HTML response time, phone/CTA near top,
quote/booking form, copyright year, agency footer credit, parked/under-construction, ad pixels.

This is a timed fetch of the HTML, not a Lighthouse/PageSpeed run (the keyless PageSpeed API
returned HTTP 429 from this environment). Treat speed as "server + HTML" time only.
Usage: python3 -I audit.py urls.txt out.jsonl   (resumable; one URL per line)
"""
import concurrent.futures as cf, json, os, re, ssl, sys, time, urllib.error, urllib.request
from html import unescape

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
PARKED = re.compile(r"domain (is|may be) for sale|buy this domain|this domain (name )?(is|has been) (registered|parked)|parked (free|domain)|sedoparking|dan\.com|afternic|hugedomains|domain parking|godaddy\.com/domainsearch|is under construction|coming soon|website coming soon|launching soon|future home of|site not found|account (has been )?suspended|default web site page|welcome to nginx|apache2 (ubuntu|debian) default|index of /", re.I)
AGENCY = re.compile(r"(?:web\s*site|web design|designed|developed|built|created|marketing|seo|powered)\s*(?:and\s+\w+\s+)?by\s*:?\s*(?:<[^>]+>\s*)*([A-Z][\w&.' -]{2,40})", re.I)
NOT_AGENCY = re.compile(r"^(wordpress|wix|godaddy|squarespace|weebly|shopify|duda|google|elementor|divi|hostinger|jimdo|webflow|yelp|thryv|the|our|us|you|a|this|complianz|flyingpress|sitemap|call|create|cookie|webador|qikcms|cloudflare|recaptcha|hcaptcha|jetpack|yoast|wp|php|astra|generatepress|kadence|oceanwp|avada|enfold|bootstrap|jquery|font|termly|iubenda|cookieyes|onetrust|accessibe|userway|privacy|terms|contact|home|get|free|request|schedule|book)\b", re.I)
BOOKING = re.compile(r"housecallpro|jobber|servicetitan|calendly|acuityscheduling|getjobber|fieldedge|squareup\.com/appointments|booking|schedule (an )?(appointment|service)|book (now|online|a )", re.I)
QUOTE = re.compile(r"(free|get a|request a|instant) (quote|estimate)|contact us", re.I)
YEAR = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", re.I)


def fetch(url):
    if not url.startswith("http"):
        url = "http://" + url
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
        body = r.read(3_000_000)
        return r.geturl(), r.status, body.decode("utf-8", "replace"), time.time() - t0


def audit(url):
    res = {"url": url}
    try:
        final, status, html, secs = fetch(url)
    except urllib.error.HTTPError as e:
        res.update(loads=False, error=f"HTTP {e.code}")
        return res
    except Exception as e:
        # retry http:// once when https fails (cert problems are a finding in themselves)
        msg = str(e)[:120]
        if url.startswith("https://"):
            try:
                final, status, html, secs = fetch("http://" + url[8:])
                res["https_error"] = msg
            except Exception as e2:
                res.update(loads=False, error=msg)
                return res
        else:
            res.update(loads=False, error=msg)
            return res
    low = html.lower()
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    vis = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()
    top = html[: max(len(html) // 4, 15000)]
    m = YEAR.findall(html)
    ag = [a.strip(" .-") for a in AGENCY.findall(html[-40000:]) if not NOT_AGENCY.match(a.strip())]
    res.update(
        loads=True, status=status, final_url=final, https=final.startswith("https://"),
        seconds=round(secs, 2), html_kb=round(len(html) / 1024),
        viewport=bool(re.search(r"<meta[^>]+name=[\"']viewport", html, re.I)),
        tel_link=("href=\"tel:" in low or "href='tel:" in low),
        tel_top=bool(re.search(r"href=[\"']tel:", top, re.I)),
        form=bool(re.search(r"<form", html, re.I)) and bool(re.search(r"<input[^>]+(email|tel|name)", html, re.I)),
        booking=bool(BOOKING.search(html)), quote_cta=bool(QUOTE.search(vis[:5000])),
        copyright_year=max(int(y) for y in m) if m else None,
        agency_credit=ag[0] if ag else "",
        parked=bool(PARKED.search(vis[:3000])) or len(vis) < 250,
        words=len(vis.split()),
        builder=next((b for b in ["wix", "godaddy", "squarespace", "weebly", "wordpress", "duda", "webflow", "shopify", "jimdo", "google sites"] if b.replace(" ", "") in low[:200000].replace(" ", "")), ""),
        meta_pixel=("fbq(" in low or "connect.facebook.net" in low),
        google_ads_tag=bool(re.search(r"AW-\d{6,}", html)),
        emails=sorted({e.lower() for e in re.findall(r"mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", html)} |
                      {e.lower() for e in re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.(?:com|net|org|us|co|biz)\b", vis)
                       if not re.search(r"example|sentry|wixpress|domain\.com|email\.com|yourname", e, re.I)})[:3],
        facebook=next(iter(re.findall(r"https?://(?:www\.|m\.)?facebook\.com/(?!sharer|share|plugins|tr\b|dialog)[A-Za-z0-9_.\-/]+", html)), ""),
        instagram=next(iter(re.findall(r"https?://(?:www\.)?instagram\.com/(?!p/|explore)[A-Za-z0-9_.]+", html)), ""),
        linkedin=next(iter(re.findall(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_\-]+", html)), ""),
        title=(re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I).group(1).strip()[:120] if re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) else ""),
    )
    return res


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    done = {json.loads(l)["url"] for l in open(out)} if os.path.exists(out) else set()
    urls = [u.strip() for u in open(src) if u.strip() and u.strip() not in done]
    with cf.ThreadPoolExecutor(8) as ex, open(out, "a") as fh:
        for i, r in enumerate(ex.map(audit, urls)):
            fh.write(json.dumps(r) + "\n"); fh.flush()
            if i % 100 == 0:
                print(i, len(urls), flush=True)
