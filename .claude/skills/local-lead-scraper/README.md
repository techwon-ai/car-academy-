# Local Lead Scraper — Claude Skill

A beginner-friendly Claude Code skill for finding local-business leads from Google Maps.

You can give Claude the full request in one prompt:

```text
Find me 20 dentists in Brisbane. Include emails where available and prioritise
independent businesses with fewer than 50 Google reviews.
```

Or simply say:

```text
Find me leads for my agency.
```

If important information is missing, the skill will ask only for what it needs, with
examples: business type, location, approximate quantity, and optional prospect preferences.

The skill will try to set up the open-source `gosom/google-maps-scraper` automatically
using its native release, so Docker is **not required by this skill's default workflow**.


## Recommended setup for viewers

Use a simple two-step setup.

### Step 1 — install the skill

With the ZIP file in Downloads, tell Claude:

```text
Install the local lead scraper skill from the ZIP file in my Downloads folder for this project.
Confirm when SKILL.md is installed.
```

Then reload/reopen the project so Claude Code can discover the skill.

### Step 2 — complete the scraper setup

After reloading, tell Claude:

```text
Set up the local lead scraper skill for this project and install everything it needs to work.
Confirm when the scraper is ready to use.
```

Claude can then follow the skill's setup instructions, detect the operating system and CPU,
install the appropriate scraper path, validate it, and prepare the project.

After setup is complete, a normal request such as:

```text
Find me leads
```

should go straight into the guided lead questions.

## Easiest install for viewers

1. Download `local-lead-scraper.zip`.
2. Leave it in your Downloads folder.
3. Open Claude Code in the folder where you want your lead files saved.
4. Tell Claude:

```text
Install the local-lead-scraper skill from the ZIP in my Downloads folder for this project.
Do not change the skill. Confirm when SKILL.md is installed.
```

5. Restart/reopen Claude Code if needed so it reloads skills.
6. Ask:

```text
Find me 10 dentists in Brisbane and include their websites and emails where available.
```

On the first run the skill will:
- detect your operating system and CPU,
- find the latest compatible native release of the scraper,
- download it directly from the upstream GitHub project,
- verify it launches,
- run a tiny validation scrape,
- then run your real search.

After that, future searches reuse the local install.

## Important: `Find me leads` always starts fresh

When you type:

```text
Find me leads
```

the skill treats that as a fresh guided search every time.

It should:
1. show the business-type multiple-choice question,
2. wait for your answer,
3. show the location question,
4. wait for your answer,
5. show the quantity question,
6. wait for your answer,
7. show the prospect-preference question,
8. and only then begin the search.

If you cancel halfway through and type `Find me leads` again, the skill starts again from
Question 1. It must not guess your niche, location, quantity, or filters, and it must not
run a sample search on your behalf.

## Guided questions in Claude Code

When you type:

```text
Find me leads
```

the skill should prefer Claude Code's built-in interactive `AskUserQuestion` experience,
so each missing detail appears as a clickable multiple-choice question one at a time.

The user can select a suggested answer or use the custom/Other option where available.
If the current Claude Code environment does not expose the interactive question tool, the
skill falls back to asking the same questions in normal text.

## Expected experience after installation

After Claude confirms that both the skill and scraper are ready, reopen/reload the project
so Claude Code discovers the skill.

Then:

```text
Find me leads
```

should go directly into the guided interview, **one question at a time**:
1. business type
2. location
3. approximate quantity
4. optional prospect preference

Normal lead searches should not repeat OS/CPU checks, Rosetta discussion, release-asset
details, or installation prompts unless the existing setup is genuinely broken.

## How prompting works

There is no required prompt template. If you already provide the business type, location,
quantity and any preferences, Claude can start directly.

If your request is vague, Claude should respond naturally:

> Absolutely. I just need a few details so I can find the right leads for you:

Then it can ask questions such as:
- What type of business? Example: dentists, physiotherapy clinics, plumbers.
- Where? Example: Brisbane, Sunshine Coast, Maroochydore, Caloundra.
- How many? Example: 10, 20, 200, 500.
- What kind of prospects? Example: email available, no website, fewer than 20 reviews,
  lower ratings, independent businesses only, or no preference.

## Where Claude should install the skill

Project-only installation:

```text
YOUR-PROJECT/
└── .claude/
    └── skills/
        └── local-lead-scraper/
            └── SKILL.md
```

A project install is recommended for the YouTube demo because it is easy to remove and
does not affect every Claude Code project.


### Email preference vs email collection

Email collection is always attempted.

If the user chooses `Email address available` as a prospect preference, that means the
final results should be filtered or prioritised for businesses where an email was found.
It does not turn email extraction on; email extraction is already on by default.


## Result quality and user experience safeguards

The skill also follows these rules:

- If fewer businesses match than requested, it returns only genuine matches. It does not
  silently broaden the location or relax filters just to reach the requested count.
- Missing numeric/filter data stays unknown. For example, a blank review count is not
  treated as zero and cannot automatically qualify for a `fewer than 20 reviews` filter.
- Successful searches finish with a simple summary showing the number of matching leads,
  how many emails were found, and where the CSV was saved.
- If the scraper errors, Claude first attempts a sensible recovery. If it still cannot
  complete the search, it gives a short plain-English explanation instead of dumping a
  long terminal log.

## What it produces

Every final CSV uses the same eight standard columns:

1. `title`
2. `category`
3. `phone_number`
4. `website`
5. `email_address`
6. `review_count`
7. `review_rating`
8. `google_maps_link`

These columns are always present. If a value cannot be found for a particular business,
the cell is left blank rather than removing the column.

Email extraction is attempted on every scrape by default. The user does not need to ask
for email addresses in the prompt.

Additional useful columns can be appended after the standard eight, such as `address` or
`why_matched` when prospect preferences are supplied.

The skill also keeps the raw scrape separately.

## Important note about native installation

The upstream gosom project officially documents Docker as its recommended installation
method and also provides downloadable binary releases for platforms. This custom skill
prefers the native-release route to make the viewer experience simpler.

Because upstream release assets and browser dependencies can change, test the skill on
your target Mac/Windows setup before recording or publishing a tutorial. If upstream does
not publish a compatible binary for a viewer's platform, the skill will explain that
instead of silently installing Docker.

## Apple Silicon and other CPU architectures

The skill checks both the operating system and CPU architecture before choosing an
upstream release.

On Apple Silicon Macs, it prefers a native `arm64` release when upstream provides one.
If the current upstream release only contains an Intel (`amd64/x86_64`) Mac binary, the
skill uses an explicit Rosetta compatibility path instead of guessing.

If Rosetta is already available, the Intel Mac binary can run through Rosetta. If Rosetta
is not available or that path fails validation, the skill automatically falls back to a
native Apple Silicon source build using a temporary project-local Go arm64 toolchain from
the official Go distribution, with checksum verification. It does not need to install Go
system-wide or force the user to install Rosetta.

A source build may still require Apple developer command-line tools on some machines. If
those are missing, Claude should explain the requirement rather than silently installing
system software. The skill must not enter passwords, bypass macOS security controls, or
silently install Docker.

The Apple Silicon Rosetta path and the native arm64 source-build path have both been
validated on Apple Silicon CI hardware, including browser startup. Real Google Maps scraping
from a home connection is still environment-dependent, and no third-party scraper can be
guaranteed to work forever on every future OS and architecture.

## First-run setup time

The first setup may take noticeably longer than later runs, especially on an Apple Silicon
Mac that needs the native source-build fallback. Claude may need to download build tools,
dependencies, and browser components before the scraper can run.

On some machines this can mean several hundred MB of downloads and roughly 10–15 minutes
of setup, sometimes with little visible output. This is only a practical estimate, not a
guarantee. Once setup is complete, later searches reuse the installed scraper and skip most
of that work.

## Search status updates

While a search is running, Claude gives brief status updates when the workflow reaches a
real stage, such as starting the scraper, cleaning/filtering finished scraper output, or
saving the final CSV.

The skill does **not** invent percentages, countdowns, ETAs, or fake row-progress numbers.
A percentage is only appropriate if the underlying tool itself exposes a reliable progress
value.

The updates are intentionally occasional and simple so users can see that work is still
happening without being flooded with technical logs.

## While a lead search is running

After you answer the guided questions, Claude should remain actively engaged with that
search until the final CSV is ready.

The scraper should run in the foreground whenever possible. If Claude Code has to use a
background task for a longer scrape, Claude should immediately monitor that task and keep
the same turn active until the scraper finishes and the results are cleaned.

You should not normally see Claude finish its turn while a line underneath still says the
scraper is running.

## Maintenance

The skill does not pin a scraper version. It looks for the latest stable compatible
release and checks the installed executable.

A quick monthly test is a sensible maintenance routine:
1. Install/use the skill in a clean test project.
2. Ask for 5–10 local businesses.
3. Confirm a CSV is produced.
4. If it works, no changes are needed.

## Credits

Uses the open-source Google Maps scraper by gosom:
https://github.com/gosom/google-maps-scraper

The upstream project is MIT licensed. This ZIP contains the Claude skill and
documentation only; it does not bundle the upstream scraper binary.

## Version

Skill: 3.2  
Prepared: 2026-09-05
