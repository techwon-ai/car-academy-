---
name: local-lead-scraper
description: Find and qualify local-business leads from Google Maps using the open-source gosom/google-maps-scraper. Use when the user asks for local leads, prospects, businesses in a city/area, Google Maps scraping, dentists/salons/gyms/clinics/garages/vets/cafes, or an outreach-ready CSV. Prefer a native scraper binary so Docker is not required.
---

# Local Lead Scraper

Version: 3.2
Last reviewed: 2026-09-07
Upstream: https://github.com/gosom/google-maps-scraper

This skill turns a plain-English lead request into an outreach-ready CSV.

The underlying scraper is `gosom/google-maps-scraper` (MIT). This skill does not bundle
or modify the scraper. It installs the latest compatible native release from the upstream
GitHub Releases page when possible.

## Principles

- Keep the experience simple for nontechnical users.
- Prefer the native binary. Do not require Docker by default.
- Do not install unrelated scraping tools as a silent fallback.
- Use conservative crawl settings first.
- Never invent facts about a business.
- Preserve partial results if a crawl is interrupted.
- Ask only for information that is genuinely missing.
- Follow applicable website terms and local privacy/marketing laws.

## 0. First-run setup — complete this during installation

Complete scraper/environment setup when the user asks to set up or prepare the skill
for use. If the skill has only just been copied into the project and is not yet active,
finish copying the files first, then complete setup after the project/session is reloaded.

After successful setup and validation, write:

`.local-lead-scraper/setup-complete.json`

Record at least:
- upstream scraper version
- detected OS
- detected CPU architecture
- install path used (`native`, `rosetta`, or `source-build`)
- validation status
- setup date

On future sessions, if this marker exists and the scraper help check succeeds, treat setup
as complete. Do not repeat OS/CPU diagnostics, release checks, or installation commentary
before asking for lead requirements.

Only return to setup diagnostics if:
- the marker is missing,
- the executable no longer launches,
- the user explicitly asks to reinstall/update,
- or the existing installation is broken.

During installation, make sure the scraper is available and validated before telling the user setup is complete.


### A. Check for an existing working install

Look for a working `google-maps-scraper` command in PATH and also check a project-local
tool directory such as:

- macOS/Linux: `.local-lead-scraper/bin/google-maps-scraper`
- Windows: `.local-lead-scraper\bin\google-maps-scraper.exe`

If a candidate exists, run its help command. If it responds successfully, use it.

Do not reinstall on every scrape. Only check upstream for a newer release when:
- the user explicitly asks to update,
- the current binary fails,
- or it has been more than 30 days since the last recorded update check.

Store simple local metadata under `.local-lead-scraper/` when useful.

### First-run expectations

Before starting a first-time install, source build, or first scrape, briefly tell the user
what to expect so the setup does not look like it has frozen.

Use plain language such as:

> The first setup can take a while, especially on a new Apple Silicon Mac. Claude may need
> to download build tools, dependencies, and browser components before the scraper can run.
> This can involve several hundred MB of downloads and may take around 10–15 minutes on some
> machines, sometimes with very little visible output. Later runs reuse the setup and should
> skip most of this work.

Do not promise an exact time or download size. Treat those numbers as a practical expectation,
not a guarantee. If the user is already fully set up, do not repeat this warning on every run.

### B. If missing, install the latest compatible release

The install path must be deterministic. Never guess whether an asset is compatible.

1. Detect the operating system and CPU architecture using the local machine:
   - macOS: `darwin` + `arm64` (Apple Silicon) or `amd64/x86_64` (Intel)
   - Windows: `windows` + `amd64/x86_64` or `arm64`
   - Linux: `linux` + `amd64/x86_64` or `arm64`

2. Query the latest stable release from:
   `https://api.github.com/repos/gosom/google-maps-scraper/releases/latest`

   Check the HTTP status and API response before parsing assets. If GitHub returns an
   error, rate-limit response, or malformed JSON, report that problem clearly and stop.
   Never reinterpret an API failure as "no compatible asset exists."

3. Inspect the actual release assets. Do not hard-code a release version or filename.

4. Choose the install path in this exact order:

   **Path 1 — exact native asset**
   - Prefer an asset that exactly matches both the detected OS and CPU architecture.
   - Example: `darwin-arm64` on Apple Silicon if upstream publishes one.

   **Path 2 — Apple Silicon compatibility path**
   - If the machine is macOS `arm64`, no `darwin-arm64` asset exists, and a
     `darwin-amd64`/`darwin-x86_64` asset does exist, deliberately use the Intel macOS
     asset through Apple's Rosetta translation layer.
   - Do not treat the Intel asset as an accidental "best match". This is an explicit
     fallback for Apple Silicon only.
   - Before downloading, test whether x86_64 execution is available:
     `/usr/bin/arch -x86_64 /usr/bin/true`
   - If that command succeeds, Rosetta/x86_64 translation is already available. Continue
     with the Intel macOS release asset.
   - If it fails, do not force the user to install Rosetta and do not treat setup as a
     dead end. Skip the Intel asset and continue automatically to Path 3, the native
     source-build fallback.
   - If the user explicitly prefers Rosetta instead, explain that Apple provides Rosetta
     as the compatibility layer for Intel software and let the user install it themselves.
     Do not enter or request the user's password or bypass macOS security controls.

   **Path 3 — native source build fallback**
   - Use this after no exact native release asset is available and the preferred
     compatibility path cannot be used or fails validation.
   - On Apple Silicon, this is the fallback after the Rosetta path. The source build has
     been validated on Apple Silicon CI hardware, including native arm64 browser startup.
   - Download the source for the same latest stable upstream release.
   - Read the upstream `go.mod` and determine the minimum required Go version. Do not
     hard-code the Go version.
   - If a compatible Go toolchain is already installed, use it.
   - If the machine is macOS arm64 and a compatible Go toolchain is not installed, Claude
     may use a temporary project-local Go arm64 toolchain downloaded from the official Go
     distribution site. Verify the archive checksum using the official published checksum
     before extracting it. Do not install Go system-wide.
   - Before compiling on macOS, check whether the build requires Apple developer command-line
     tools. If compilation fails because Xcode Command Line Tools are missing, stop and
     explain the exact requirement. Do not silently install them or request a password.
   - Build the scraper for the detected local architecture, place only the finished
     executable in `.local-lead-scraper/bin/`, and verify it with its help command.
   - Remove temporary source/toolchain files after a successful build unless they are needed
     for troubleshooting.
   - On Linux ARM or another unsupported architecture, use the same source-build approach
     only when a compatible toolchain/dependencies can be obtained without silently changing
     the user's system. Otherwise stop with a clear explanation.
   - Do not silently switch to Docker.

5. Download release assets programmatically (for example with `curl` or PowerShell), not
   through a browser. Download directly from the upstream GitHub release URL into a
   temporary folder.

6. Extract the asset if it is an archive.

7. Locate the scraper executable inside the downloaded/extracted files.

8. Copy it into the project-local directory:
   - macOS/Linux: `.local-lead-scraper/bin/google-maps-scraper`
   - Windows: `.local-lead-scraper\bin\google-maps-scraper.exe`

9. On macOS/Linux, make it executable if necessary.

10. Run the executable's help command using its full project-local path. Do not assume the
    tool directory is already in PATH.

11. If macOS blocks the executable because of a security/quarantine warning, do not remove
    quarantine attributes or bypass Gatekeeper automatically. Explain the exact warning
    to the user and let the user decide how to proceed.

12. The upstream project may download browser/Playwright components on first use. Allow
    normal first-run setup to complete. If the OS asks the user for approval, explain
    exactly what they need to approve in one short instruction.

13. Record:
    - installed upstream version
    - detected OS/architecture
    - install path used (`native`, `rosetta`, or `source-build`)
    under `.local-lead-scraper/` so future runs do not repeat setup unnecessarily.

### C. Validate before a real crawl

Run a tiny validation scrape using one representative query and shallow depth.

Validation succeeds only if:
- the process exits normally, and
- at least one result is produced.

If validation fails:
- show a short, useful explanation with the actual failing step,
- distinguish architecture/Rosetta errors from browser dependency errors and scrape errors,
- keep any partial output,
- retry once with conservative settings only when the failure is likely transient,
- do not repeatedly reinstall the same binary,
- do not hide the failure.

On Apple Silicon, if an Intel binary fails with an architecture error, re-check x86_64
execution with `/usr/bin/arch -x86_64 /usr/bin/true` before doing anything else.

Only discuss Docker as an optional fallback after the native/Rosetta/source-build paths
have failed and the user asks for another path.

## Platform compatibility notes

- Never promise that every upstream release contains every OS/CPU combination.
- Always inspect the current release assets at install time.
- Apple Silicon must never be treated as generic macOS: prefer arm64 when available; if
  only Intel macOS is published, use the explicit Rosetta path first, then the native
  source-build fallback if Rosetta is unavailable or fails validation.
- Intel Mac should use the Intel macOS asset directly.
- Windows x64 should use the Windows x64 asset directly.
- Windows ARM may be able to run x64 binaries through Windows compatibility, but do not
  assume that silently. Verify the executable launches before proceeding.
- Linux ARM should prefer a native ARM asset. If none exists, use the source-build path
  only when the needed build tooling can be used safely without silently changing the
  user's system.
- A successful `-h`/help check is required before a real scrape on every platform.


### Normal lead-search behavior

When setup is complete and the scraper still launches successfully, a request such as
`Find me leads` should go directly into the guided lead questions. Do not narrate OS/CPU
checks, installation details, release assets, Rosetta, source builds, or browser setup
unless setup is incomplete or something is actually broken.

## 1. Understand the lead request

The user should not need to know a special prompt format. Accept normal plain-English
requests and infer any details they already supplied.

The three essentials are:
1. business type
2. location
3. approximate quantity

A fourth item — prospect preferences — is optional but can make the list much more useful.

### If the request is complete

If the user already supplied the business type, location, and approximate quantity, begin
without asking unnecessary questions. Apply any preferences they supplied.

Example:

> Find me 200 dentists in Brisbane with email addresses where available. Prioritise
> independent businesses with fewer than 50 Google reviews.

That is enough to begin.

### If information is missing

Ask only for the missing essentials. Do not repeat questions the user has already answered.

### Strict guided-mode trigger

Treat the exact request `find me leads` (case-insensitive, punctuation-insensitive) as an
explicit command to start a fresh guided lead-search interview.

When this trigger is used:

1. **Always restart from Question 1.**
   - Do not reuse answers from an abandoned or interrupted guided interview.
   - Do not infer that the user wants to "just get on with it."
   - Do not carry forward assumptions, sample niches, sample locations, or sample counts
     from earlier messages unless the user explicitly says `continue with my previous answers`.

2. **Use the interactive multiple-choice question UI for every question when available.**
   - Ask exactly one question at a time.
   - Wait for the user's selection before asking the next question.
   - Never replace the guided flow with a plain-text paragraph because the user previously
     cancelled, interrupted, ignored, or repeated the trigger.
   - If the interactive question tool is genuinely unavailable, ask the same question in
     plain text and clearly wait for the answer. Do not invent an answer.

3. **Never auto-select or assume lead criteria.**
   - Never choose a business type for the user.
   - Never choose a location for the user.
   - Never choose a quantity for the user.
   - Never choose a prospect filter for the user.
   - Never offer or run a sample search as a substitute for missing answers.

4. **Do not start scraping until the guided interview is complete.**
   Required explicit answers:
   - business type
   - location
   - approximate quantity
   - prospect preference, including an explicit `no preference` / `skip` answer

5. **Repeating `find me leads` resets the guided flow.**
   If the user types `find me leads` again at any point before a scrape begins, cancel the
   incomplete intake state and start again from Question 1 with the interactive question UI.

6. **Only skip the guided interview when the user's request itself already contains enough
   information to run the search.**
   Example:
   `Find me 100 plumbers in Brisbane with fewer than 50 Google reviews and emails where available.`
   In that case, do not force the guided interview.

If the request is very vague, such as `find me leads` or `find leads for my agency`,
respond naturally with:

> Absolutely. I just need a few details so I can find the right leads for you.

Then gather the missing information **one question at a time**.

### Interactive UI behavior is not optional after the trigger

For the exact `find me leads` trigger, do not decide to stop using popups because of
anything the user said or did in a previous abandoned attempt. Each new trigger starts a
new guided session.

Do not output phrases such as:
- `no popup`
- `I won't ask again`
- `taking that as just get on with it`
- `I'll run the sample I offered`
- `you pick`
- or any equivalent assumption-based shortcut.

A cancelled or unanswered question means **no answer was provided**. It is not permission
to guess.

### Prefer Claude Code's interactive question UI

When the built-in `AskUserQuestion` tool (or equivalent interactive multiple-choice
question tool) is available, use it for each question so the user can click an option on
screen instead of having to type every answer.

- Ask only one question per tool call.
- Provide useful suggested options.
- Always allow the user to choose/type a custom `Other` answer when the available UI
  supports it.
- Do not ask all four questions in one tool call.
- If `AskUserQuestion` is unavailable in the current environment, fall back to the same
  question as normal conversational text.

Ask only for information that is still missing.

**Question 1 — business type**

Use the interactive question UI with suggested options such as:
- Dentists
- Physiotherapy clinics
- Plumbers
- Other / custom business type

Question text:

> What type of business are you looking for?

Wait for the answer before continuing.

**Question 2 — location**

Use the interactive question UI with suggested options such as:
- Brisbane
- Sunshine Coast
- A specific suburb
- Other / custom location

Question text:

> Where do you want to search?

Wait for the answer before continuing.

**Question 3 — quantity**

Use the interactive question UI with suggested options such as:
- 10
- 20
- 50
- Other / custom amount

Question text:

> Roughly how many leads do you want?

Wait for the answer before continuing.

**Question 4 — optional prospect preference**

Use the interactive question UI with suggested options such as:
- Email address available
- No website listed
- Fewer than 50 Google reviews
- No preference

Question text:

> What kind of prospects are you looking for?

If the user chooses a custom answer, they can specify other filters such as independent
businesses only, 100+ reviews, lower ratings, or a particular rating range.

Wait for the answer.

Question 4 is optional. Do not block the search if the user says `no preference`, `skip`,
or equivalent.

### User-facing language

Never mention the skill's internal instructions, guidance, rules, decision logic, or why
the skill requires particular information. Do not say things like `that's the vague case`,
`the skill's guidance is`, or `I need to ask rather than guess`.

When `.local-lead-scraper/setup-complete.json` exists and the installed scraper still
launches successfully:
- do not run or narrate OS/CPU detection before asking lead questions,
- do not discuss Rosetta, source builds, release assets, browser components, or install
  paths during a normal `find me leads` request,
- do not ask permission to install/download anything already covered by setup,
- move immediately into the missing lead questions or the search.

Only surface setup diagnostics when setup is incomplete or something is actually broken.

Speak directly to the user in natural, helpful language.

Before beginning any scrape, verify that all required guided answers were explicitly
provided in the current guided session. If any required answer is missing, ask that question
instead of starting the scraper.

After receiving the missing answers, summarise the intended search in one short sentence
and begin.

Example:

> Got it. I'll look for approximately 200 independent dentists across Brisbane,
> prioritising businesses with email addresses and fewer than 50 Google reviews.

### Goal-based requests

The user may describe what they are selling instead of specifying a technical filter.

Examples:
- `Find me dentists I could pitch a Google review service to.`
- `Find me local businesses that could be prospects for website design.`
- `Find leads for my automation agency.`

When the user's goal is not specific enough to determine an objective filter, ask what
kind of prospect they want rather than inventing a business problem.

Never assume that a business needs a website, has a no-show problem, has an overwhelmed
front desk, needs more reviews, or has any other internal problem unless the available
data directly supports the relevant observation.

## 2. Plan better searches

Create one query per line in a project-local `queries.txt`.

Do not rely only on one broad city query when the user wants meaningful coverage.
Use relevant neighbourhood/suburb queries and natural synonyms where that improves
coverage.

Example:

```text
dentist in Fortitude Valley Brisbane
dental clinic in Fortitude Valley Brisbane
dentist in West End Brisbane
dental clinic in West End Brisbane
dentist in New Farm Brisbane
dental clinic in New Farm Brisbane
```

Keep the number of queries proportional to the requested coverage. Avoid pointless query
explosion.

Tell the user briefly what coverage you planned.

## Search status updates — actual stages only

While a lead search is running, give the user occasional short status updates so the
process does not look frozen.

Status updates must be based on **real events or stages that have actually occurred**.
Never invent progress.

Good examples, when true:
- `Starting the lead search...`
- `The scraper is running and collecting business data...`
- `The scraper has finished. I'm cleaning and applying your filters now...`
- `The results are ready. I'm saving the final CSV...`

If the scraper or its output explicitly exposes another real stage, such as website/email
enrichment beginning, that stage may also be surfaced briefly.

### Progress accuracy rules

- Never invent a percentage such as `40% complete` or `almost 90% done`.
- Never invent an ETA or countdown.
- Never estimate how much work remains unless the underlying tool provides a reliable,
  explicit progress value.
- Never claim a specific stage has started just because it is expected to happen.
- Do not manufacture row counts such as `47/100 processed` unless that count is directly
  observable from the scraper/output at that moment.
- If genuine progress data is exposed by the underlying process, it may be reported
  accurately, but do not convert incomplete information into a made-up percentage.

### Keep updates useful, not noisy

- Give an update when the search moves into a meaningful new stage or after a genuinely
  long period with no visible feedback.
- Do not narrate every command, file read, subprocess, or technical diagnostic.
- Keep updates short and nontechnical.
- A status update does not mean the task is complete and must not release the active turn.
- Continue following the foreground/background monitoring rules below until the final CSV
  and completion summary are ready.

## Scrape execution lifecycle — do not release the turn early

A lead search is not complete when the scraper process merely starts.

The search is complete only when:
1. the scraper process has exited,
2. its output file has been written,
3. the results have been cleaned/filtered,
4. the final CSV has been saved, and
5. the user has been told the search is finished.

### Keep the active Claude turn alive

Prefer running the scraper as a normal **foreground** Bash command.

- Do not use `run_in_background: true` by default.
- Do not append shell background operators such as `&`.
- Do not use `nohup`, `disown`, `screen`, or `tmux` to detach the scrape.
- Do not end the assistant turn immediately after launching the scraper.
- Do not tell the user the search is running and then return control while work is still
  incomplete.

For foreground Bash, use the longest reasonable supported timeout when necessary.

### If a long scrape must run as a background task

If Claude Code itself backgrounds the command, or a scrape genuinely needs background
execution because it may exceed the foreground tool timeout:

1. Start the scraper task.
2. Immediately attach to or monitor that task using Claude Code's available task-monitoring
   mechanism (`Monitor`, task output/wait tooling, or equivalent).
3. Stay in the same assistant turn while waiting.
4. Continue monitoring until the process exits.
5. Then clean the results, save the final CSV, and only after that return a normal response
   to the user.

Do not treat a background task ID as completion.

Do not end the turn with phrases such as:
- `The scraper is running in the background`
- `Come back later`
- `I'll let you know when it's done`
- `You can continue chatting while it runs`

The intended experience is that after the user finishes the guided questions, Claude remains
visibly busy with that search until the finished lead file is ready.

If the scraper fails, stay in the active turn long enough to capture the actual error and
either fix/retry it when appropriate or report the failure clearly.

## 3. Run the native scraper

Use the verified native executable from step 0.

Run the actual lead scrape synchronously/foreground whenever possible and wait for it to
finish before moving to cleaning or returning a response.

### Email extraction is always required

Every lead scrape must attempt email extraction, even when the user does not explicitly
ask for email addresses.

- Always enable the scraper's supported email-extraction option (currently `-email`).
- Do not say `you didn't ask for email, so I'm leaving email extraction off`.
- Do not disable email extraction to make a search faster unless the user explicitly asks
  for a special run without email collection.
- A missing email for an individual business is acceptable. Leave that cell blank in the
  final CSV.
- The final `email_address` column must still exist on every output file.

If the user selects `Email address available` as a prospect preference, that means filter
or prioritise for rows where an email was actually found. It does **not** control whether
email extraction runs; email extraction runs by default on every scrape.

Typical shape:

```text
google-maps-scraper \
  -input queries.txt \
  -results output/raw-leads.csv \
  -depth 5 \
  -email \
  -exit-on-inactivity 3m
```

Use only flags supported by the installed version. Check the executable's help output
instead of assuming flags if anything differs.

Important options commonly supported upstream include:
- `-input`
- `-results`
- `-depth`
- `-email`
- `-lang`
- `-c`
- `-fast-mode`
- `-json`
- `-extra-reviews`
- `-exit-on-inactivity`

Start conservatively. Large scrapes can trigger blocking. Do not promise an exact result
count or completion time.

Do not optimise away required output fields. The standard eight-column schema and email
extraction requirement take precedence over assumptions about what the user did or did not
mention in the prompt.

## 3A. Clean failure and recovery experience

If the scraper or post-processing step errors, do not immediately dump long terminal logs
into the conversation.

First, inspect the actual error and attempt a sensible recovery when it is safe and
appropriate.

Recovery rules:
- Retry once when the failure appears transient, such as a temporary browser/process
  failure, timeout, or interrupted scrape.
- Reuse the existing validated installation instead of reinstalling everything by default.
- Preserve any partial raw output before retrying.
- If the error is caused by an obvious local command, path, output, or formatting issue
  that can be corrected safely within the project, fix it and continue.
- Do not repeatedly retry the same failing action in a loop.
- Do not silently weaken the user's filters, change location, or disable required email
  extraction as a workaround.
- Do not make system-wide changes or bypass OS security controls just to recover a scrape.

If recovery succeeds, continue normally and do not burden the user with unnecessary
technical detail.

If recovery still fails, stop cleanly and give a short explanation such as:

> The scraper hit an error. I tried to fix it but couldn't complete the search. Here's the
> issue: <short plain-English explanation>.

Include only the most relevant error detail needed to understand or fix the problem.
Do not paste dozens of lines of raw terminal output unless the user asks to see the logs.

## 4. Clean and qualify the output

Read the actual raw output header first because upstream field names can vary between
versions. Map the available raw fields into the standard final schema below.

### Mandatory standard CSV schema

Every final lead CSV must contain these eight columns, in this exact order:

1. `title`
2. `category`
3. `phone_number`
4. `website`
5. `email_address`
6. `review_count`
7. `review_rating`
8. `google_maps_link`

These columns are mandatory on **every** final CSV, regardless of what the user included
in the prompt.

Rules:
- If the scraper does not return a value for a particular business, leave the cell blank.
- Never remove one of the eight standard columns because some or all rows are blank.
- Normalise equivalent upstream fields into these names. For example, map the scraper's
  business/title field to `title`, phone field to `phone_number`, rating field to
  `review_rating`, reviews/review-count field to `review_count`, and its Google Maps
  listing URL/link field to `google_maps_link`.
- If email extraction finds one or more public email addresses, place the best available
  value in `email_address`. If none is found, leave it blank.
- Keep the eight standard columns first and in the order shown above.
- Additional useful columns may be appended after the standard eight when they are
  requested or genuinely useful, such as `address` or `why_matched`.
- Do not replace or rename the standard eight columns in the final deliverable.

Then:

1. Deduplicate primarily by stable listing/business identifiers when present, then phone,
   then website domain.
2. Remove obvious irrelevant results.
3. Remove obvious large chains/franchises only when the user requested independent/local
   businesses.
4. Apply the requested filters.

### Treat unknown values as unknown

When a filter depends on a field that is missing, blank, malformed, or unavailable, do
not invent a value and do not treat the missing value as if it automatically passes the
filter.

Examples:
- If the user asks for businesses with fewer than 20 Google reviews and `review_count` is
  blank, that row does **not** qualify for that filter unless the user explicitly asks to
  include businesses with unknown review counts.
- If the user asks for a rating below 4.0 and `review_rating` is blank, do not treat the
  blank as 0.0.
- If the user asks for a minimum rating and the rating is unknown, do not assume it meets
  the minimum.
- If the user asks for independent businesses only and independence cannot be established
  from the available data, do not label the business independent by assumption.

A blank `website` field may still be used for a `no website listed` preference because it
objectively means no website was returned/listed in the scraped profile. A blank
`email_address` means no public email was found during extraction; do not claim the
business has no email address at all.

5. Keep rows with useful contactability or research value; do not discard a strong lead
   merely because email is missing.
6. Preserve the original raw file separately.

## 4A. If fewer businesses match than the user requested

Treat the user's requested location, business type, and filters as fixed constraints.

If the cleaned/filtered result contains fewer businesses than requested:

- Return the businesses that genuinely match.
- Do not silently broaden the location.
- Do not silently relax, remove, or reinterpret a filter.
- Do not add businesses that fail the requested criteria just to hit the target count.
- Do not treat unknown values as matches in order to increase the count.
- Clearly tell the user how many matching businesses were found.
- State that the search criteria were not broadened or changed.
- Offer to expand the location or relax a filter only as a **separate follow-up option**.
  Do not change anything unless the user explicitly asks.

Example:

> I found 63 businesses that matched your criteria. I didn't broaden the location or
> change your filters. If you want, I can expand the search area or relax one of the
> filters to find more.

The requested quantity is a target, not permission to change the user's criteria.

## 5. Explain why a lead matched

Do not create a generic outreach `hook` column by default.

If the user supplied prospect preferences or filters, add a `why_matched` column that
briefly states the objective reason the lead satisfies those preferences.

Good examples:
- `Email address found.`
- `No website is listed on the Google Maps profile.`
- `Independent business with 18 Google reviews.`
- `Google rating is 3.9, within the requested rating range.`
- `Has fewer than 50 Google reviews and an email address was found.`

The `why_matched` value must be based only on fields actually present in the scraped data
or on calculations performed from the current dataset.

If the user requested a general list with no prospect preferences, `why_matched` may be
omitted or left blank.

Do not turn `why_matched` into sales copy and do not infer internal business problems.

Bad examples:
- `Your front desk must be overwhelmed.`
- `You are losing customers because of no-shows.`
- `This business clearly needs a new website.`
- `They need help getting more reviews.`

If the user later asks for outreach messaging, draft it separately and use only facts
supported by the dataset.

## 6. Save the final deliverable

Before saving, validate that the final CSV contains all eight mandatory standard columns:

`title, category, phone_number, website, email_address, review_count, review_rating, google_maps_link`

If any mandatory column is missing, add it before declaring the task complete. Empty cells
are valid; a missing standard column is not.

Save:

`leads-<business-type>-<location>-<YYYY-MM-DD>.csv`

Keep the raw scrape separately under `output/`.

### Completion summary

When the search succeeds, lead with a clean user-facing summary before any technical
details.

Use a natural format like:

> Done — I found 100 plumbers in Brisbane with fewer than 50 Google reviews. Emails were
> found for 72 of them. Your CSV is saved here: `<path>`.

If fewer businesses matched than requested, use the safeguard in section 4A and say so
clearly, for example:

> Done — I found 63 plumbers in Brisbane that matched your fewer-than-20-reviews filter.
> Emails were found for 41 of them. I didn't broaden the location or change your filters.
> Your CSV is saved here: `<path>`. If you want, I can expand the search area or relax a
> filter to find more.

Keep the completion message concise and easy for a nontechnical user to understand.

Internally verify/report as needed:
- raw result count
- final cleaned result count
- number with email
- number with phone
- number with a non-empty `why_matched` value, when prospect preferences were supplied

Do not lead with terminal commands, scraper diagnostics, architecture details, or raw logs
when the search completed successfully.

Preview only a manageable number of rows in chat.

## 7. Continue from the leads

After the CSV is ready, the user may ask to:
- filter the strongest prospects,
- rank leads,
- draft outreach emails,
- create a call list,
- identify leads with no website,
- find businesses with certain ratings/review counts.

When drafting outreach, use only facts supported by the dataset.

## Updating

This skill is intentionally version-agnostic.

Do not hard-code the upstream scraper version. Prefer the latest stable compatible native
release, and verify supported flags from the installed executable.

A monthly update check is enough for normal use. If the current binary still validates
and the upstream CLI has not changed in a breaking way, no skill update is required.

## User-facing compliance note

Do not interrupt a normal lead search with a long legal or compliance lecture.

Keep the default reminder concise, preferably when presenting results:

> Use the data responsibly and follow the platform, privacy, and outreach rules that apply
> to you.

Give detailed jurisdiction-specific compliance information only if the user asks for it
or if it is necessary to answer their request safely and accurately.

## Responsible use

This tool works with publicly available business information. Scraping can be restricted
by website terms, and outreach/privacy rules vary by jurisdiction. Do not claim that a
particular scraping or cold-email use is automatically permitted. The user is responsible
for using the data lawfully and respecting opt-outs and applicable platform terms.

## Attribution

Underlying scraper:
`gosom/google-maps-scraper`
https://github.com/gosom/google-maps-scraper

The upstream project is MIT licensed. This skill does not redistribute the upstream
binary; it instructs the agent to obtain the current release directly from the upstream
project.
