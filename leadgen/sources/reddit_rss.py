#!/usr/bin/env python3
"""Owner requests on Reddit via public RSS search feeds (old.reddit requires login; RSS does not).
Keeps posts from the last 60 days. Usage: python3 -I reddit_rss.py out.json
"""
import datetime as dt, html, json, random, re, sys, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
SUBS = ["smallbusiness", "forhire", "Entrepreneur", "florida", "tampa", "orlando", "jacksonville", "Miami", "fortlauderdale",
        "StPetersburgFL", "Sarasota", "fortmyers", "lakeland", "Tallahassee", "lawncare", "roofing", "HVAC", "Plumbing",
        "pressurewashing", "smallbusinessowners", "sweatystartup", "PestControl", "pools", "CleaningBusiness", "web_design"]
QUERIES = ['"need a website"', '"website for my" business', '"recommend" marketing agency', '"google business profile" help',
           'hiring web designer', '"looking for" website designer', '"need help" marketing', '"local seo"']
NS = {"a": "http://www.w3.org/2005/Atom"}
cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)
out, seen = [], set()
multi = "+".join(SUBS)  # one multireddit search per query instead of one request per subreddit
for q in QUERIES:
    for sort in ("new", "relevance"):
        url = f"https://www.reddit.com/r/{multi}/search.rss?" + urllib.parse.urlencode({"q": q, "restrict_sr": "on", "sort": sort, "t": "month", "limit": 100})
        try:
            x = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30).read()
            root = ET.fromstring(x)
        except Exception as e:
            print("err", q, str(e)[:80], flush=True); time.sleep(20); continue
        for e in root.findall("a:entry", NS):
            link = e.find("a:link", NS).get("href")
            when = e.find("a:updated", NS).text
            if link in seen or dt.datetime.fromisoformat(when.replace("Z", "+00:00")) < cut:
                continue
            seen.add(link)
            body = html.unescape(re.sub(r"<[^>]+>", " ", (e.find("a:content", NS).text or "")))
            cat = e.find("a:category", NS)
            out.append({"sub": cat.get("term") if cat is not None else "", "query": q, "title": e.find("a:title", NS).text, "url": link,
                        "date": when[:10], "author": (e.find("a:author/a:name", NS).text if e.find("a:author/a:name", NS) is not None else ""),
                        "body": re.sub(r"\s+", " ", body)[:1500]})
        json.dump(out, open(sys.argv[1], "w"), indent=1)  # rewritten after every request so nothing is lost
        print(q, sort, len(out), flush=True)
        time.sleep(random.uniform(4, 8))
print(len(out), "posts in last 60 days")
