"""Minimal client for the public Google Maps search endpoint used by gosom's -fast-mode.

Why not just the scraper: in this environment gosom's normal mode returns blank fields (place pages
don't render through the proxy) and fast mode drops single-business answers (it skips items[0]),
which is exactly what a name lookup returns. Field indices mirror gosom/google-maps-scraper v1.9.2.
"""
import json, random, time, urllib.parse, urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
PB = ("!4m12!1m3!1d3826.902183192154!2d{lon:.4f}!3d{lat:.4f}!2m3!1f0!2f0!3f0!3m2!1i600!2i800!4f{zoom:.1f}!7i20!8i{offset}"
      "!10b1!12m22!1m3!18b1!30b1!34e1!2m3!5m1!6e2!20e3!4b0!10b1!12b1!13b1!16b1!17m1!3e1!20m3!5e2!6b1!14b1!46m1!1b0"
      "!96b1!19m4!2m3!1i360!2i120!4i8")


def g(o, *idx):
    for i in idx:
        if not isinstance(o, list) or i >= len(o) or o[i] is None:
            return None
        o = o[i]
    return o


def _url_of(raw):
    if raw and raw.startswith("/url?q="):
        raw = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query).get("q", [raw])[0]
    return raw or ""


def parse_business(b):
    addr = g(b, 2) or []
    place_id = g(b, 78) or ""
    return {
        "title": g(b, 11) or "",
        "categories": [str(c) for c in (g(b, 13) or [])],
        "website": _url_of(g(b, 7, 0)),
        "phone": (g(b, 178, 0, 0) or "").replace(" ", ""),
        "rating": g(b, 4, 7),
        "reviews": int(g(b, 4, 8) or 0),
        "address": ", ".join(str(x) for x in addr),
        "city": g(b, 183, 1, 3) or "",
        "zip": g(b, 183, 1, 4) or "",
        "state": g(b, 183, 1, 5) or "",
        "data_id": g(b, 10) or "",
        "place_id": place_id,
        "has_hours": bool(g(b, 34, 1) or g(b, 203)),
        "description": g(b, 32, 1, 1) or g(b, 154, 0, 0) or "",
        "status": g(b, 34, 4, 4) or "",
        "maps_url": f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else "",
    }


def search(query, lat, lon, zoom=12, offset=0, retries=2):
    params = {"tbm": "map", "authuser": "0", "hl": "en", "q": query,
              "pb": PB.format(lat=lat, lon=lon, zoom=zoom, offset=offset)}
    url = "https://maps.google.com/search?" + urllib.parse.urlencode(params)
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US"})
            raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
            raw = raw[raw.index("\n") + 1:]
            items = g(json.loads(raw), 0, 1) or []
            out = []
            for it in items:
                b = g(it, 14)
                if isinstance(b, list) and g(b, 11):
                    out.append(parse_business(b))
            return out
        except Exception:
            if attempt == retries:
                raise
            time.sleep(5 + 5 * attempt)


def pause(lo=1.5, hi=3.5):
    time.sleep(random.uniform(lo, hi))
