# Running the California roofers lead search locally

The search is prepared and committed. It does **not** run in a Claude Code cloud
session — that environment's proxy blocks Google's Maps JavaScript bundle, so the
Maps app never loads and every business field comes back empty. Run it on your own
machine, where that restriction does not apply.

**Search:** roofers · California statewide · target 200 leads · no website listed
**Coverage:** 93 queries across 69 California cities, depth 15

## 1. Get the code

```bash
git clone https://github.com/techwon-ai/car-academy-
cd car-academy-
git checkout claude/happy-volta-hk3h8c
```

## 2. Install the scraper

The scraper binary is deliberately **not** in this repo (~76MB, and it must match
your CPU). The skill installs it. Open Claude Code in this folder and ask:

```
Set up the local lead scraper skill for this project and install everything it needs.
```

The skill detects your OS and CPU and picks an install path in order: a matching
native release, then Rosetta on Apple Silicon if only an Intel build exists, then a
source build using Go. It verifies the binary with a help check before declaring
success.

Notes for macOS:

- A source build needs Xcode Command Line Tools. If they are missing the skill stops
  and tells you, rather than installing system software silently.
- On first run the scraper downloads Playwright browser components (~115MB).
- First setup can take a while with little visible output. Later runs reuse it.

Confirm the binary works before continuing:

```bash
./.local-lead-scraper/bin/google-maps-scraper -h
```

## 3. Run the search

```bash
./run-roofers-california.sh          # scrapes; writes output/raw-roofers-california.csv
python3 clean-roofers-california.py  # writes leads-roofers-california-<date>.csv
```

`python3` only needs the standard library — nothing to `pip install`.

To change the target count:

```bash
python3 clean-roofers-california.py output/raw-roofers-california.csv 50
```

## What to expect

This is a long run. 93 queries at depth 15, with email extraction fetching business
websites, takes substantial time. Do not expect it to finish in minutes.

**It may return fewer than 200.** Most roofers list a website, so a large raw pool is
needed to reach 200 genuine no-website matches. If it comes up short, the cleaner
reports the real number and leaves your criteria alone. It will not broaden the
location or relax the filter to hit the target — that is your decision, not the
script's.

## Output columns

The eight mandatory columns, then `address` and `why_matched`:

`title, category, phone_number, website, email_address, review_count, review_rating,
google_maps_link, address, why_matched`

## How the no-website filter stays honest

A blank `website` can mean two very different things: the business genuinely lists no
website, or extraction failed for that listing. Counting the second as a match would
invent facts.

So `clean-roofers-california.py` drops any listing with no business name **before**
filtering — no name means extraction failed, which makes the blank website unknown
rather than absent. Only listings that extracted successfully and still have no
website are counted.

## Responsible use

This works with publicly available business information. Scraping can be restricted by
platform terms, and outreach and privacy rules vary by jurisdiction — California has
its own under CCPA/CPRA. Use the data responsibly and follow the rules that apply to
you.

Underlying scraper: [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) (MIT).
