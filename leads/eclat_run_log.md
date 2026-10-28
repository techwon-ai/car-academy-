# Eclat lead sheet: run log

Built 2026-10-28 by `leadgen/build_eclat.py`. Replaces the first sheet, which was rejected: it held established
businesses, rows without phones, and scores below 7.

## What every row in `eclat_leads.csv` has passed

1. **New business.** Registered with the Florida Division of Corporations between Aug 25 and Oct 27, 2026
   (one row is a filing dated to start Jan 1, 2027). Florida address. Named person on the filing.
2. **Already live on Google.** A Google Maps listing whose distinctive name words are identical to the filing,
   in the same city (or Google returned only that one business). Partial or fuzzy matches are rejected.
3. **Not an old business re-filing paperwork.** The listing has 25 reviews or fewer.
4. **High-confidence phone.** The listing's phone is a Florida area code and belongs to the filing's region,
   and no other business name among the ~6,600 Florida trade listings collected uses that number.
5. **Not closed.** Re-queried on Google at build time: same listing, same phone, no "Temporarily closed" or
   "Permanently closed" anywhere in its data.
6. **Real gap.** No website on the profile, or the link is broken, parked, only a social page, or fails a
   real-phone audit (rendered in mobile Chromium).
7. **Score 7** on the original rubric: +3 new filing, +2 website gap, +1 profile still bare (0 reviews or no
   hours), +1 owner and phone.

Owner confidence: 62 high (manager/president/owner on the filing), 2 medium (only a person registered agent is
listed, which on a single-member LLC is usually the owner).

## Funnel

| Step | Count |
|---|---|
| New FL trade filings in the window (LLC/corp, or a DBA filed by a person) | 4,470 parsed, 3,682 checked |
| No exact Google listing | -3,312 |
| No named person on the filing | -679 (not counted in the 3,682) |
| Listing isn't a trade business / no FL phone / area code outside the region / phone shared | -18 / -43 / -28 / -1 |
| Already has 26+ reviews (established business) | -39 |
| Website works and passes the audit | -80 |
| Profile already has reviews and hours | -62 |
| Website blocks automated checks (can't judge it, so not claimed) | -19 |
| Failed the live re-check (listing no longer returned for the name / marked closed) | -15 / -1 |
| **Final** | **64** |

Trades: cleaning 28, lawn/landscaping 23, pressure washing 7, pool 5, plumbing 1. New roofing, HVAC and most
plumbing businesses already launch with a working website, so almost none qualify.

## Not verified, and why

- **Social activity in the last 60 days.** Facebook redirects to login and Instagram returns 429 + login from
  this environment; checking would require logging in, which the brief rules out. Not claimed.
- **Google review dates.** Google's reviews endpoint returns 403 here. Not claimed.
- The 60-day state filing plus a Google profile that is still live and unchanged today is the freshness evidence
  this sheet relies on. Spot-check a row's Facebook yourself before calling if social activity matters.
