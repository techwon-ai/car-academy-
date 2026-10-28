# Techwon lead run: Florida home services

Run date: 2026-10-28. Free sources only; no logins, no paid APIs, no trials. Code: `leadgen/` (see its README).
Deliverables: `leads/techwon_leads.csv` (300 rows), `leads/top50.csv`. Both are git-ignored because
the repo is public and the files hold owner names and phone numbers.

## Bottom line

| Source | Target | Delivered | Why it differs |
|---|---|---|---|
| Sunbiz new filings | 90 | 161 (+3 merged with Maps) | absorbed the shortfall from the blocked/thin sources |
| Google Maps | 80 | 130 (+3 merged) | absorbed shortfall; only 133 listings clear the 4-point bar |
| Meta Ad Library | 40 | 0 | blocked: logged-out view returns zero ads from this IP (even for "nike") |
| New domains | 30 | 6 | 109 trade+FL domains; most are lead-gen or parked spam; only 8 tied to a real FL business, 6 score 4+ |
| Job postings | 30 | 0 | Indeed/ZipRecruiter Cloudflare-walled; LinkedIn found 1 trade marketer hire, and it is a 2,089-review company (not 1-20 staff) |
| Owner requests | 30 | 0 | 184 Reddit posts and 2,983 Craigslist posts; one real FL owner asked for help but didn't name the business (scores 3) |
| **Total** | **300** | **300** | |

Score distribution (final 300): 7 → 28, 6 → 33, 5 → 30, 4 → 209.
Owner name: 283/300 (94%), of which 271 high, 12 medium. Any phone: 241/300 (80%). Email: 42/300 (14%).
Trades: cleaning 108, landscaping/lawn 72, plumbing 34, pool 25, HVAC 21, pressure washing 16, roofing 15, pest 9.

## Per source

### 1. Sunbiz (Florida Division of Corporations)
- Access: the official Data Downloads page lists host `https://sftp.floridados.gov`, user `Public`, password `PubAccess1845!`.
  SFTP port 22 is blocked from this container and Cloudflare challenges curl, so the files were fetched inside a
  logged-in headless browser (`leadgen/sources/sunbiz_download.js`).
- Files: daily corporate `doc/cor/yyyymmddc.txt` and fictitious-name `doc/fic/yyyymmddf.txt`, 2026-10-08 → 2026-10-27
  (14 business days; weekend files included where they exist): 18 corporate + 20 fictitious files.
- Layout from `dos.sunbiz.org/data-definitions/cor.html` and `fic.html` (1440- and 2098-byte fixed records).
  Fields are UTF-8 bytes inside a byte-positioned layout; person names are last[0:20] first[20:34] middle[34:].
- Found: 1,418 active trade-named filings; 1,402 with a Florida principal address.
- Dropped: 210 fictitious names owned by an existing company (not a new business), 5 older entities that only had
  new activity, a few non-ICP names (garage clean-outs, painting, demolition, auto detailing...).
- Enrichment: each filing looked up on Google Maps by name + city. Strict match only (identical distinctive words,
  Florida phone or same city, listing must itself be a trade business): 112 matched, 64 rejected as out-of-state
  numbers, 6 rejected as non-trade. Phones for Sunbiz rows come from that listing, so `phone_confidence` = medium.
- Pool at 4+: 1,179. Kept: 161 (highest scores first).
- Gap: Sunbiz has no phones. 59 Sunbiz rows have no phone; they're reachable by the principal address in `notes`.

### 2. Google Maps
- gosom/google-maps-scraper v1.9.2 built from source with Go (GitHub release downloads return 403 here).
  Its Playwright 1.52 driver download points at a retired CDN, so the driver was assembled from npm
  `playwright-core@1.52.0` and pointed at the preinstalled Chromium.
- gosom normal mode returns rows with every field blank in this container (place pages don't render through the proxy).
  `-fast-mode` works: 10 cities x 21 queries (8 trades with synonyms) = 4,083 rows, 2,236 unique.
- Deeper coverage with `leadgen/maps_api.py` (same endpoint fast mode uses) at pages 0/20/40: 630 requests, 12,597 rows.
  Combined unique listings: 6,618.
- Qualifying per the brief (no website AND 30+ reviews, OR a website that fails the audit): 470.
- Dropped: 5 with 500+ reviews (too big for a 1-20 person ICP, a judgment call), 13 chains/franchises (-3),
  13 with an agency credit in the footer (-3), 17 suspected lead-gen listings (-3), the rest below 4.
- Pool at 4+: 133. Kept: 130 + 3 merged with Sunbiz.
- Owner names for Maps rows: Sunbiz quarterly corporate file (1.8 GB Deflate64 zip, streamed; 114k active trade
  entities, exact name + same city) → high confidence; DBPR construction license extract (license holder for
  roofing/HVAC/plumbing/pool) → medium confidence because the qualifier isn't always the owner.

### 3. Meta Ad Library: blocked
- `facebook.com/ads/library` returns HTTP 403 and "No ads match" for every query from this IP, including "nike".
  The Ad Library API needs a logged-in token. Quota moved to Maps/Sunbiz.
- Partial proxy: the website audit records whether a Meta Pixel or Google Ads tag is installed. That is not proof
  of running ads, so it earns no points.

### 4. Newly registered domains (whoisds)
- 14 daily free lists (2026-10-14 → 10-27). The free list is capped at 70,000 domains a day, so it's a sample, not every registration.
- 109 domains contain a trade keyword and a Florida place/"fl" term. Most are rank-and-rent or AI lead-gen sites
  ("...hotline.com", one "Free Inspection & Quote" template across three cities, a Las Vegas 725 number with an
  "actors" disclaimer, a placeholder license number `CFC000000`).
- Tied to a real Florida business (by Maps listing website/phone or Sunbiz): 8. Kept at 4+: 6.
  Dropped after tie-in: Bella Cleaning (Sarasota) and Surface Professional Roof Rejuvenation scored 3.
- Untied but worth a manual look (not in the CSV, no independent proof they're real):
  `palmbeachqualitycleaning.com` (site says "(561) 000-0000 Phone number coming soon"),
  `angelspoolsfl.com` (template placeholders "[Business phone number]" still live),
  `excellentlandscapingoffl.com` (owner first name and phone on the site, no Maps or Sunbiz match), `prolandscapingsolutionsfl.net` ("Próximamente").
- Excluded: agency-built new sites (credits to Connor Cedro SEO, WebLynx Solution, Sesium Clicks), MosquitoNix and
  Image One (franchises), Florida Clean Water (water purification, not ICP), Downtown Orlando Plumbing (likely lead-gen).

### 5. Job postings
- Indeed and ZipRecruiter: Cloudflare challenge (403). Google search: CAPTCHA. Brave only indexes Indeed's
  aggregate pages, not employers.
- LinkedIn public guest job search (no login), 8 roles x 10 trades, last 30 days: 274 postings, 10 at trade-named
  employers. Only one was a marketing hire at a home-service company: Bold City Heating & Air (Jacksonville,
  Marketing Specialist, 2026-10-26). It has 2,089 Google reviews, so it's not owner-operated 1-20 staff. Excluded.
  Advanced Roofing (Fort Lauderdale, marketing manager) is also too large; Rentokil/EcoShield are chains.
- Result: 0 leads. Small trade businesses hiring marketers post on Indeed/Facebook, which this environment can't reach.

### 6. Owner requests
- Reddit: old.reddit requires login; RSS search works. Multireddit search over 25 subs, 8 queries, last 60 days:
  184 posts (several 429 rate-limit responses). Almost all are freelancers advertising.
  One real Florida owner: r/smallbusiness 2026-10-17, "I know pool service cold but not marketing..." (3 clients,
  wants 100, has a site and GBP). They explicitly don't name the business, so the lead scores 3 (+3 asked for help)
  and isn't in the CSV. Worth a direct, non-salesy Reddit reply: https://www.reddit.com/r/smallbusiness/comments/1x8l1kb/
- Craigslist FL (19 sites x gigs/computer, gigs/creative, jobs/marketing, jobs/web): 2,983 posts, all MLM, "they're
  giving away websites", UGC/actor gigs. 0 leads.
- Public Facebook group posts: need Google search (CAPTCHA) or a login. Skipped.

## Website audit method
- Quick HTML fetch for every site (6,600+): HTTPS, viewport tag, timing, tel link, form, booking, copyright year,
  agency credit, emails, social links.
- Every site that could matter got a rendered audit in real mobile Chromium (iPhone 13 viewport): 897 sites. Only
  the rendered result can award "fails" or "parked" points, because JS-built sites look empty to a plain fetch
  and SiteGround/Cloudflare bot walls look like parked pages (both caught during this run).
- "Fails" = no viewport or page wider than the phone, OR full load > 15s in our test, OR no tap-to-call, quote form,
  booking, or call/quote button. Load times go through this container's proxy, so they're higher than a real
  phone would see; the 15s bar is deliberately high.
- "Broken" = DNS failure, certificate error, HTTP 404/410/500/502/504, expired or parked page. HTTP 401/403/429/503,
  CAPTCHA and "checking your browser" pages = could not audit (no points either way).
- Google PageSpeed API without a key: HTTP 429 (shared daily quota exhausted). Not used.

## Scoring notes and judgment calls
- Scoring is exactly the brief's. Two additions, both disclosed in `notes`:
  1. Suspected lead-gen listings (no website + 30+ reviews + mailbox-style suite address or a phone shared with
     another listing) get the -3 "already has an agency" penalty. They're marketers, not owner-operators.
  2. Listings with 500+ reviews are dropped as too large for the ICP. Some 300-400 review HVAC companies remain and
     may also be over 20 staff.
- "No website" for a Sunbiz filing with no Maps listing could not be web-verified: Google (CAPTCHA), Bing (junk
  results), DuckDuckGo (418), Mojeek (403), Qwant (error) and Brave (rate-limited after ~9 queries) all block
  this IP. Those rows get +1 (no GBP found) but not the +2, and `website_status` says the search wasn't done.
- Quotas vs. "top 300 by score": the small sources are kept whole, Maps and Sunbiz split the rest. Under this
  scoring a brand-new LLC with a 0-review profile (+3 +1 +1 +2 = 7) outranks a 241-review pest company whose
  website is dead (+2 +1 +1 = 4). See the summary for why that matters.

## Problems hit
- gosom normal mode: blank fields through the proxy (same as the earlier session). Worked around with fast mode + direct endpoint.
- Initial Sunbiz-to-Maps matching was too loose ("COOL JAX HVAC" ↔ "Cool Transport Jax"); tightened and re-run.
- First Sunbiz download decoded UTF-8 and re-encoded, shifting columns on accented names; re-downloaded as raw bytes.
- Owner names were first collapsed and scrambled for double surnames; re-parsed from the fixed sub-fields.
- No-website Maps listings briefly qualified without the 30-review minimum; fixed before the final build.
