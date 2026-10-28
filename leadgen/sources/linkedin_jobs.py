#!/usr/bin/env python3
"""Job postings: LinkedIn's public guest job-search endpoint (no login), last 30 days, Florida.
Indeed and ZipRecruiter return Cloudflare challenges here, and Brave only indexes Indeed's
aggregate pages, so LinkedIn guest search is the free source that names individual employers.
Keeps postings whose *company name* matches an ICP trade. Usage: python3 -I linkedin_jobs.py out.json
"""
import html, json, os, random, re, sys, time, urllib.parse, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from trades import classify

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
ROLES = ["marketing coordinator", "social media", "web designer", "digital marketing", "marketing manager", "marketing assistant", "content creator", "marketing specialist"]
TRADES = ["roofing", "hvac", "air conditioning", "plumbing", "landscaping", "lawn care", "cleaning", "pressure washing", "pest control", "pool"]


def g(c, p):
    m = re.search(p, c, re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""


out, seen = [], set()
for role in ROLES:
    for trade in TRADES:
        for start in (0, 25):
            qs = urllib.parse.urlencode({"keywords": f"{role} {trade}", "location": "Florida, United States", "f_TPR": "r2592000", "start": start})
            try:
                req = urllib.request.Request("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?" + qs, headers={"User-Agent": UA})
                t = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
            except Exception as e:
                print("err", role, trade, e); time.sleep(20); continue
            cards = re.findall(r"<li>.*?</li>", t, re.S)
            for c in cards:
                company = g(c, r'base-search-card__subtitle">(.*?)</h4>')
                link = g(c, r'base-card__full-link[^>]*href="([^"?]+)')
                rec = {"title": g(c, r'base-search-card__title">(.*?)<'), "company": company,
                       "company_url": g(c, r'base-search-card__subtitle">\s*<a[^>]*href="([^"?]+)'),
                       "location": g(c, r'job-search-card__location">(.*?)<'), "date": g(c, r'datetime="([^"]+)'),
                       "url": link, "query": f"{role} {trade}"}
                key = (company.lower(), rec["title"].lower())
                if key in seen:
                    continue
                seen.add(key)
                rec["trade"] = classify(company)
                out.append(rec)
            time.sleep(random.uniform(2.5, 5))
            if len(cards) < 25:
                break
json.dump(out, open(sys.argv[1], "w"), indent=1)
hits = [r for r in out if r["trade"]]
print(len(out), "postings,", len(hits), "with trade-named employers")
for r in hits: print(r["company"], "|", r["title"], "|", r["location"], "|", r["date"])
