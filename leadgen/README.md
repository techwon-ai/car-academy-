# Eclat lead pipeline (Florida home services)

Free sources only. Every step is resumable and writes its working data under `output/work/`
(git-ignored). Final lists land in `leads/` (also git-ignored: they hold owner names and phone
numbers, and this repo is public). See `leads/eclat_run_log.md` for what the last run found.

| Step | Script | Notes |
|---|---|---|
| Sunbiz daily filings | browser login to `https://sftp.floridados.gov` (public credentials on the DOS Data Downloads page), then `sunbiz_parse.py` | SFTP port 22 is blocked here and Cloudflare challenges curl, so files are fetched inside a Playwright session |
| Sunbiz quarterly officers | `enrich/sunbiz_quarterly_index.py` | 1.8 GB Deflate64 zip, streamed through `unzip -p`; owner names for Maps listings |
| DBPR licensees | `enrich/dbpr_index.py` | free CILB extract; license holder (qualifier) names, medium confidence |
| Google Maps | `maps/run_maps.sh` (gosom `-fast-mode`) and `maps/maps_paged.py` | gosom normal mode returns blank fields through this proxy; `maps_api.py` calls the same endpoint fast mode uses and also handles single-business answers |
| Sunbiz to Maps match | `enrich/sunbiz_maps_lookup.py` | strict name match is re-checked in `build_leads.py` (`pick_listing`) |
| New domains | whoisds free daily list (base64 date URL) + `enrich/audit.py` | the free list is capped at 70,000 domains a day |
| Jobs | `sources/linkedin_jobs.py` | LinkedIn guest endpoint; Indeed and ZipRecruiter are Cloudflare-walled |
| Owner requests | `sources/reddit_rss.py`, `sources/craigslist.js` | Reddit RSS multireddit search; Craigslist FL gigs and jobs |
| Website audit | `enrich/audit.py` (quick HTML) then `enrich/audit_render.js` (real mobile Chromium) | only the rendered audit can award "fails" or "parked" points |
| Build (current) | `build_eclat.py <scratch dir>` | only new filings already live on Google with a verified FL phone, a real website gap, and score 7; writes `leads/eclat_leads.csv` |
| Build (v1, superseded) | `build_leads.py <scratch dir>` | the broader first sheet; kept for reference |

Environment notes for this container: `PLAYWRIGHT_NODEJS_PATH=$(command -v node)` and a
`PLAYWRIGHT_BROWSERS_PATH` that maps Chromium 1169 to the preinstalled build are needed for gosom,
because its Playwright 1.52 driver download points at a retired CDN.
