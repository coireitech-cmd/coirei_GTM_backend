# Market research benchmark and quality gates

A small live benchmark that can be rerun with the same commands, plus the
acceptance criteria a research run is judged by. Definitions:
`benchmarks/market/cases.json`. Evaluator: `gtm_crawler/market/benchmark.py`
(tested by `tests/test_market_benchmark.py`). Results:
`benchmarks/market/results/<date>/`.

```bash
# Needs GTM__SEARCH__API_KEY and GTM__IDENTITY__USER_AGENT_CONTACT (never commit them).
python -m gtm_crawler.market.benchmark run benchmarks/market/cases.json out/
python -m gtm_crawler.market.benchmark evaluate benchmarks/market/cases.json out/   # re-score
# Through the API and a worker (empty, migrated database):
GTM__DATABASE__URL=postgresql+psycopg://.../gtm_crawler_bench \
  python -m gtm_crawler.market.benchmark run benchmarks/market/cases.json out/ --via-service
```

`out/` gets one JSON per case (the report, wall time) and `raw/` (the fetched
pages, so every quote can be re-checked) and `summary.json`. Only summaries
and labels are committed: raw pages are third-party content.

## Cases

| Id | Kind | Scope |
|---|---|---|
| `ind-ev-charging-europe` | industry | EV charging infrastructure, Europe; sizing USD, 2024 |
| `ind-plant-based-meat-us` | industry | Plant-based meat, United States; sizing USD, 2024 |
| `prod-invoicing-freelancers` | product | "Billfold", invoicing for freelancers; competitors FreshBooks, Wave |
| `prod-email-marketing-ecommerce` | product | "Sendly", email marketing for online stores; competitors Mailchimp, Klaviyo |
| `sizing-crm-india-msme` | sizing | CRM software, India; bottom-up seats for MSMEs plus the evidence-derived total |

Each case: 4 queries, 5 results per query, 5-6 sources, 240 s. About 20
search queries per full run.

The sizing case's customer count is checked against an authoritative
source, read in this session: 633.88 lakh = 63,388,000 MSME units ("Total
Number of Units", ALL INDIA, NSS 73rd round, July 2015 - June 2016), RBI
Handbook of Statistics on Indian States, Table 127
(<https://www.rbi.org.in/Scripts/PublicationsView.aspx?id=21517>). The
other inputs are labelled assumptions. The expected TAM/SAM/SOM per scenario
in `cases.json` were computed by hand and are re-derived from the inputs by
`test_case_file_is_valid_and_its_expected_sizing_is_the_arithmetic`.

## What is checked

### Deterministic gates (pass = zero failures)

Each is exact; a single failure is a defect, not noise.

| Gate | Rule |
|---|---|
| provenance | every HTML and PDF quote verifies against its stored artifacts (`verify.verify_report`): HTML re-parsed from the stored page (rendered page for browser renders); PDFs re-extracted from the stored file, or at least found at their page and offsets in the stored extraction (`by_status` counts each outcome) |
| citations | every evidence id cited by a finding, metric summary, contradiction, sizing input, exclusion or score exists in the report |
| numeric | every money figure (sizes, forecasts, prices) re-parsed from its own text by a separate parser (`reparse_money`, not the extractor's regex) gives the same value and currency, and its text is inside the excerpt |
| scope | every figure behind a metric summary has that summary's geography, segment, unit, subset and qualifier; every figure behind an evidence-derived total has the sizing's target geography (unstated only when none was requested), segment, currency and year, measure "market", is not a part, another market or a qualified market |
| sizing_math | every TAM/SAM/SOM in every scenario recomputed from the request's own inputs (cents) |
| unsupported | quoted findings carry their excerpt; derived figures' bounds are values stated by their evidence; user statements and hypotheses are LOW; direct/derived findings cite something |
| status | COMPLETED only with evidence, no warning/error issue, no failed/skipped source and every research question answered; PARTIAL always states reasons; FAILED only without evidence |
| ad_claims | no finding that cites a captured ad speaks, in its own words (quotes of the cited excerpts removed), of spend, budgets, impressions, reach, CTR, ROAS, conversions, targeting, audiences or performance (`market.ads.FORBIDDEN`); provenance also re-checks every ad quote against its stored capture (docs/ad-intelligence-design.md) |

The gates check internal correctness. They cannot tell that a correctly
quoted figure describes the wrong market: that is the analyst review below,
and it is where the benchmark found most defects.

### Reported, not gated

Coverage (sources tried / used, retrieval rate, domains, research questions
answered), competitor pages fetched and rendered, price points, duplicate
excerpts, contradictions, latency, search queries and their cost (with
`cost.search_query_usd` set; unknown otherwise), the sizing's selected and
excluded figures, and the case's own expectations (status, hand-computed
bottom-up values, competitor pages fetched).

### Analyst review (qualitative)

Every parsed market figure and price point of a run, read against its
excerpt: correct when value, currency, kind, year and scope (geography,
segment, measure, market definition) are what the sentence says. Labels:
`results/2026-10-21/labels.json`. Done by the implementing agent, not an
independent reviewer; no AI-generated report was used as a reference.

## Baseline (2026-10-21, live Exa search and real sites)

Three runs of the same cases: run 1 right after the M7 scope fix, run 2
after fixing what run 1's review found, run 3 after fixing what run 2's
review found. Run 2 was also repeated through the API and a worker
(`results/2026-10-21/run2-api-worker.json`, two cases: identical results).

| Measure | Run 1 | Run 3 |
|---|---|---|
| Deterministic gates | 7/7 pass in every case | 7/7 pass in every case (also via API + worker) |
| Status | 5 x PARTIAL, reasons stated | 5 x PARTIAL, reasons stated |
| Figures: value and currency correct (analyst) | 64/64 | 58/58 |
| Figures: fully correct incl. kind and scope (analyst) | 31/64 (48%) | 49/58 (84%) |
| Evidence-derived totals of the right market (analyst) | 1/2 | 2/2 |
| Latency per case (4 queries) | 12-17 s | 5-8 s |
| Search queries per run | 20 | 20 |

Run 3 per case:

| Case | Tried / used (rate) | Questions answered | Evidence | Total selected | Notes |
|---|---|---|---|---|---|
| EV charging, Europe | 7 / 5 (0.71) | 7/8 | 99 | USD 13.8 B for 2024, Europe (aloraadvisory.com), not verified | contradiction kept: Grand View USD 5.69 B vs Alora USD 18.5 B for 2025 (Europe); IEA 403 |
| Plant-based meat, US | 11 / 3 (0.27) | 1/8 | 2 | none: no eligible figure; sizing not calculated (PARTIAL) | 4 x 403 (GFI, AgFunder), BLS robots-disallowed, a redirect loop |
| Invoicing for freelancers | 10 / 5 (0.50) | 6/7 | 34 | - | 4/4 competitor pages fetched; 10 price points |
| Email marketing for stores | 14 / 9 (0.64) | 7/7 | 233 | - | 4/4 competitor pages; 12 list prices; 4 x 403 |
| CRM, India (sizing) | 9 / 6 (0.67) | 7/8 | 111 | USD 2.315 B for 2024, "India CRM Software Market" (marketresearchfuture.com), not verified | bottom-up = hand-computed values; cross-check 6.6x apart, flagged |

The CRM total is the sentence "the India CRM Software Market size was
estimated at 2315.0 USD Million in 2024" (the provenance gate re-derived
it from the stored page). It is one commercial publisher's teaser figure:
reported, not verified.

### Defects the benchmark found (all fixed, each with a test from the live sentence)

Run 1: a different market definition ("India CRM marketing services market")
selected as the CRM-software total; "the market generated a revenue of" read
as company revenue; verbs and adjectives ("we estimate that the market",
"fastest growing regional market") read as another market; segment growth
rates filed under the wrong segment; an investment filed as a market size;
market revenue later in a sentence read as company revenue; savings, gift
cards, spend thresholds and credits filed as prices; fees without their
"per transaction" unit; `/pricing/` tried again as `/pricing/pricing`.
Run 2: capex filed as a price; another market's spending ("cloud IT
infrastructure") not flagged. Details: `labels.json`.

### Open (run 3)

* A service type's or application's growth rate read as the market's
  ("CRM Migration and Modernization ... 12.74% CAGR"): such sub-markets are
  not in the segment lexicon (3 figures). Growth rates are not used in sizing;
  they feed the growth score.
* A fee unit is lost when a parenthesis sits between amount and "per
  transaction" (4 figures).
* A forecast year before the figure ("from 2026 to 2032, reaching nearly
  USD 4.12 Bn") is not attached (1 figure).
* "Capture the lion's share of the US$58.4 billion 2030 market" flagged as
  part of a larger figure (1 figure).
* Coverage: commercial and research sites answer 403, official statistics
  are PDFs (read since runs 4-6, see below) or robots-disallowed: the plant-based case answered
  1 of 8 questions.

## PDF runs (runs 4-6, same day, same cases and configuration)

PDF reading was added after run 3. Runs 4-6 use the same `cases.json`
(budgets unchanged), so a difference comes from the code, from the
search results (source availability), or from the code and the budget
together. Summaries: `results/2026-10-21/run4-pdf-before-fixes.json`,
`run6-pdf-final.json`.

* **Run 4** (first with PDFs): every live PDF came back `pdf_failed`. The
  extraction child wrote its JSON in the Windows console code page
  (cp1252): "₹", "≤", "●" crashed it, accented characters broke decoding.
  The fixtures were ASCII, so the tests had not seen it. Fixed (UTF-8
  bytes) with two regression tests.
* **Run 5**: crashed on a PDF table whose cells held paragraphs (a 2,000+
  character "row" failed validation and the exception ended the run).
  Fixed: such rows are not rows, and one source's failing extraction is now
  a limitation and a warning. A chart's labels on RVO p. 27 gave five
  "price points" (the price rule's "plan" matched "Plant-based"): fixed.
* **Run 6** (final): all gates pass in every case; every PDF quote
  `verified_reextracted`.

| Case | PDFs read (pages, excerpts, figures) | Questions | Change vs run 3 and its cause |
|---|---|---|---|
| EV charging, Europe | EU Alternative Fuels Observatory (16 pp., 20 excerpts, 0 figures); ITF-OECD 403 | 7/8 (7/8) | **lost the evidence total**: the government PDF (tier 1, ranked first) took the slot of the Alora page that stated the only eligible Europe figure (code + budget); RMI/PwC swaps are search results. Fixed in run 7 (source selection) |
| Plant-based meat, US | RVO "Proteins Study" (84 pp., 47 excerpts, 6 figures); OECD PDF robots-disallowed | 7/8 (1/8) | **coverage from 1 to 7 questions**, 2 -> 49 excerpts (code); still no US market-size figure, sizing not calculated |
| Invoicing for freelancers | none met | 6/7 (6/7) | 34 -> 38 excerpts (search results) |
| Email marketing for stores | a 20-page e-commerce marketing report on a CDN (40 excerpts, 6 share figures) | 7/7 (7/7) | 233 -> 211 excerpts (search results) |
| CRM, India (sizing) | MoSPI release (6 pp., 1 excerpt); BCC Research teaser (13 pp., 2 excerpts); 2 MoSPI releases irrelevant | 7/8 (7/8) | same totals (USD 2.315 B top-down, bottom-up unchanged); 111 -> 44 excerpts: off-topic official PDFs took slots (code + budget) and the result list changed (availability) |

Analyst review of the PDF figures of run 6: the shares quote their source
correctly (e.g. RVO p. 56: "In 2019 NPD found that 16 percent of Americans
say they "regularly" use plant-based alternatives ...", a third party's
survey figure as reported by RVO); "$5/lb" keeps no "/lb" unit; a research
report's own price ("US$ 4,650.00 single user licence") is filed as a CRM
price point. No PDF in these runs gave a market-size figure, so the M7 scope
rules were exercised on PDFs by the fixtures only.

Latency: 7-19 s per case, 37 s for the case with the 84-page PDF (run 3:
5-8 s).

## Source selection (run 7, same cases and budgets as run 6)

**Why the EV total disappeared in runs 5-6.** Sources were ordered once,
before collection, by search rank + 3 x (tier - 1); search titles and dates
were thrown away; and every fetched page counted against `max_sources`,
including irrelevant and empty ones. With `max_sources = 6` the EV run
spent its slots on an ONS indicator page (0 excerpts), an ONS map
(irrelevant), a World Bank landing page (0), the EU observatory PDF (20
excerpts, no figures), Grand View and BCG; the Alora page whose title was
"Europe EV charging infrastructure market outlook 2030" and which stated the
only eligible Europe total was never fetched. Irrelevant MoSPI PDFs used
CRM slots the same way. The fix is question-driven selection with explicit
budgets (`docs/MARKET_ENGINE_IMPLEMENTATION.md`, "Source selection and
budgets").

**Live, run 6 -> run 7** (`results/2026-10-21/run7-selection.json`; same
`cases.json`, same budgets; new budget fields at their defaults: `max_pdfs`
2, `max_pdf_pages` 50, `max_extraction_pages` 150). All 7 gates pass in
every case in both runs.

| Case | Questions | Evidence total | Excerpts | Sources with evidence / attempts without | Latency | Cause of the change |
|---|---|---|---|---|---|---|
| EV charging, Europe | 7/8 -> 7/8 | **none -> USD 13.8 B (2024, Europe)** | 79 -> 139 | 3 / 4 -> 5 / 5 | 7.5 -> 19.1 s | selection (code): the Alora outlook ranked first by title and scope in wave 2; 3 failures (S&P, ITF-OECD, MDPI) recovered by alternatives |
| Plant-based meat, US | 7/8 -> **6/8** | none -> none | 49 -> 36 | 2 / 10 -> 2 / 9 | 37 -> 55 s | budget default (code): the RVO PDF is read to page 50 now; the business-models question was answered only on its pages 54-55. Its own search was never sent (`max_queries` 4) |
| Invoicing for freelancers | 6/7 -> 7/7 | - | 38 -> 87 | 5 / 7 -> 6 / 3 | 19 -> 23 s | selection (code); search results also differ |
| Email marketing for stores | 7/7 -> 7/7 | - | 211 -> 176 | 9 / 4 -> 8 / 3 | 9 -> 20 s | selection stopped early ("remaining candidates add little"); search results differ |
| CRM, India (sizing) | 7/8 -> 8/8 | USD 2.315 B both | 44 -> 97 | 4 / 4 -> 4 / 0 | 17 -> 12 s | selection (code): no off-topic MoSPI release fetched |

Search queries: 20 per run in both. Sources with evidence count search
results and competitor pages; attempts without evidence are failed,
blocked, unreadable, irrelevant, duplicate or empty. Latency rose where more
sources were read (and waves wait for their slowest page).

**Blind labels** (`results/2026-10-21/labels-blind-run6-run7.json`): every
parsed figure of runs 6 and 7 (117) and the evidence totals (3), shuffled
with the run hidden, labelled by a separate agent from the excerpt alone.
Stricter than `labels.json` (transaction fees and add-on products are not
the product's price), so compare runs within this file only.

| | Run 6 | Run 7 |
|---|---|---|
| Figures fully correct | 38/57 (67%) | 42/60 (70%) |
| Market sizes and forecasts correct | 6/6 | 20/23 |
| Evidence totals / correct scope | 1 / 1 | 2 / 2 (EV recovered) |

Most errors are price extraction, not selection: transaction and set-up
fees filed as prices (13), add-on products' prices (9), units missing their
basis (5), growth rates without their end year (5), investment or a
report's own price as market figures (7; one item can have two).

**Old vs new on the same fixture scenarios**
(`benchmarks/market/compare_selection.py`,
`results/2026-10-21/compare-selection.jsonl`; the engine of `4b91711` vs this
milestone, one source each unless said):

| Scenario | 4b91711 (rank + tier) | This milestone |
|---|---|---|
| Market-estimate page vs a long contextual PDF (1 slot) | PDF collected, no total | estimate page, total found |
| Irrelevant high-ranked tier-1 pages (1 slot) | water-utility page, nothing | estimate page, total found |
| Failed top source, then an alternative (1 slot) | total found | total found |
| Three PDFs for 2 slots, the best ranked last | two contextual PDFs, no total | the market-size PDF first, total found |

Targeted regression scenarios (`tests/test_market_selection.py`, scripted
web, deterministic; questions answered of 8 / total / sources with evidence
/ attempts without):

| Scenario | Result |
|---|---|
| HTML estimate vs long contextual PDF | 2/8, total, 1 / 0; the PDF SKIPPED with its rationale |
| Official PDF with the only estimate | 4/8, total from the PDF (page 1), 1 / 1 |
| Irrelevant PDF with a high search rank | 2/8, total, 1 / 0; "its title names none of the subject's terms" |
| Duplicate sources | 3/8, total, 2 / 1 (the duplicate), an alternative tried |
| PDFs competing for a limited allowance | 2/8, total, 1 / 0; the others "over the PDF allowance" |
| Failed high-ranked source, then an alternative | 2/8, total, 1 / 1; PARTIAL "1 source(s) failed" |
| Different questions needing different sources | 6/8, total, 4 / 0; the pricing and regulation pages, not the pages every query returned |
| Budget exhaustion | 2/8, no total, 1 / 0; PARTIAL, a budget warning, a reason per open question |

What these show and what they do not: the scenarios and the old-vs-new
runs are built to exercise one mechanism each; the live run is five cases
on one day, with search results that changed between runs. Internal gates
passing says the report is consistent with its evidence, not that its
conclusions are right; the labels are one agent's reading.

## Pricing (price terms; fixture set, live set, runs 8-9)

`benchmarks/market/pricing/` (labels: `README.md`):

* `cases.json`, fixtures: 175 cases, 187 pages, 360 labelled prices and 109
  labelled non-prices in 23 categories (setup vs subscription, transaction
  fees, percentage fees, add-ons, credits and minimum spend, discounts and
  promotions, usage, goods units, tiers, tables, JSON-LD, quote-based,
  currency ambiguity, tax, included allowances, starting and upper-bound
  prices, monthly vs annual billing, missing units, outdated pages,
  conflicting sources, non-price numbers, JavaScript-rendered pages). Pages
  written and labelled by an agent that did not see the extractor.
* `live.json`: every sentence and table row with an amount in the stored
  HTML of live runs 6-7 (448 items), labelled blind by two agents (ids
  split between them). Their one disagreement (the Wave page's bare "$":
  USD assumed vs unclear) is not scored. A table cell was harvested twice
  (as its own item and inside its row) and is scored once, in the row: 456
  labelled prices are scored. The raw pages are the runs' raw stores, not
  committed.

`score.py fixtures` / `score.py live --raw <dir>` with PYTHONPATH at the code
to score (the old code: `git archive 2b188b8`). A price matches a label by
amount (and percent-ness); precision = matched / extracted, recall =
matched / labelled; exact-unit = currency, percent, period, billed, basis
and its quantity all as labelled. The old code has only a unit string, so
old-vs-new is also given on what both can be judged on (currency, period,
basis). Live: the labellers saw only each item's text, so a plan, product,
billing or date read from around it (a heading, the page) that the text
does not state is not judged (102 such values); 9 records that repeat an
already-matched labelled text (an add-on line on every plan card, a cell
repeated in its row) are counted apart; 23 records outside every labelled
item (JSON-LD, mostly) are not scored. Results: `results/{old,new}_{fix,live}.json`.

| Set | Code | Labelled | Extracted | Precision | Recall | Exact unit | Currency + period + basis |
|---|---|---|---|---|---|---|---|
| Fixtures | old (2b188b8) | 360 | 129 | 93.8% | 33.6% | (unit string only) | 76.0% |
| Fixtures | new | 360 | 328 | 97.9% | 89.2% | 86.3% | 86.9% |
| Live (runs 6-7 pages) | old | 456 | 59 | 96.6% | 12.5% | (unit string only) | 91.2% (57 matched) |
| Live (runs 6-7 pages) | new | 456 | 351 | 95.7% | 73.7% | 75.3% | 90.2% |

New code, field accuracy of matched prices (fixtures / live): type 82.9 /
88.4, currency 99.7 / 100, period 94.4 / 99.7, billed 97.2 / 84.8, basis
92.2 / 90.5, bound 99.1 / 96.4, plan 65.4 / 43.2, product 79.1 / 44.4,
included 90.7 / 89.6, tax 97.8 / 100, promotional 97.8 / 98.2. Main
content only (live): precision 95.1%, recall 73.9%. The JavaScript render
trigger fires on all 7 fixture pages that need rendering.

Remaining errors:

* Fixture false positives (7, all labelled non-prices): a gift card "from
  $25", a credit top-up "from $5", a report's price on a market page, an
  average customer's spend, the largest customer's spend, a competitors'
  average, an ROI claim per month. Four are typed unknown (never compared);
  three read as monthly subscriptions and would enter a comparison.
* Fixture misses (39): percentage fees without fee words ("International
  cards: an additional 1.5%"), amounts with no currency ("29/month",
  "Price: 15.00", "Basic 49"), a table whose header row holds the plans and
  whose price row has bare numbers, tier lines ("Up to 1 lb: $4.50"),
  quote wording not in the rules ("available on request"), JSON-LD offers
  whose visible text differs, credit packs, regular prices beside sale
  prices.
* Live false positives (15): FreshBooks cells repeated across items (8),
  duplicate Invoicera cards with no type (3), Wave fee lines, a report's
  member price.
* Live misses (120): 66 are a market-research publisher's price for its own
  report licence ("€5388EUR$5,850USD£4,543GBP", "Single User License 2999
  4500 USD"), on one line with no separators; 30 are a wallet review page
  that states prices with no price words ("At $129 it is ..."); the rest
  are comparison sentences without cues ("Mailjet exits at $9"), split
  elements ("$ 4" + ".60") and card lines.
* Plan and product (live 43% / 44%): inline elements are joined without a
  space by the parser ("EmailOctopusPro"), FreshBooks marks its add-ons
  only with a CSS class, and comparison sentences name a product and plan
  in one phrase ("Kit Creator runs $39 to $139").

How far to trust these numbers: the rules were tuned while reading the
false positives and misses of both sets (there is no held-out set), so both
scores are optimistic; the labels are AI agents' readings, not a human
gold standard; matching is by amount, so a matched price can still have
wrong terms (that is what the field accuracies measure).

Live runs 8 and 9 (same cases and budgets as run 7; run 9 is the final
code, after an independent review's fixes; run 9's summary:
`results/run9-pricing.json`): all gates pass in both, the
numeric gate now re-reading each price's amount and currency (run 9: 131 in
the email-marketing case, 89 invoicing, 29 EV charging, 10 CRM India) and
the provenance gate re-resolving each price's context quotes. Price points
per case, run 7 -> run 9: email marketing 16 -> 131, invoicing 10 -> 86, EV
charging 0 -> 12, CRM India 1 -> 2. The plant-based case fell from 36
excerpts to 2 because the rvo.nl PDF that gave 34 of them answered
"blocked" in runs 8-9 (the site, not the code: the same source list was
tried). In run 9's reports the pricing section compares, for example, "USD
per month, subscription" prices of several vendors and lists FreshBooks'
add-ons ("USD per month, add on") and Wave's card fees (percent and fixed,
per transaction) as their own sets; remaining mistakes seen there are the
plan and product names above and some FreshBooks add-on lines still filed
as subscriptions (no heading or table row says they are add-ons).

**Why the plant-based case lost its 34 excerpts (checked 2026-10-22).** Run
9's source list records the rvo.nl PDF as `blocked: robots_unreachable`: its
robots.txt could not be fetched, and the robots policy reads a network error
or a 5xx on robots.txt as disallow-all. From this network, both
`https://www.rvo.nl/robots.txt` and the PDF time out after 20 s, so the host
is unreachable here. This is not a code change. The blocked source is listed
as blocked and its earlier excerpts are not reused. The alternatives did not
fill the gap: three gfi.org PDFs were skipped as over the per-domain budget
after gfi.org pages answered 403; the OECD PDF's robots.txt disallows
crawlers; the remaining candidates were below the value floor (0.08); and 4
question searches were never sent (`max_queries` 4). Page targeting (below)
does not change this: it decides which pages of a PDF are read, not whether
an unreachable host can be fetched.

## Price attribution (2026-10-22, development data)

The parser can now put a space at inline-element boundaries
(`ParseSettings.inline_spacing`; market sources record
`text_version: html-text-2`; the crawler's text is unchanged). Each price
records how its plan or product was found (`attribution`: clause, heading,
table, structured_data, or uncertain when several plans are on the page and
none is the price's own). The scorer now judges records, not only fields: a
price is right on `product_plan` only when both plan and product match, and on
`full_record` only when currency, percent, period, basis, quantity, plan and
product all match.

Fixtures (175 cases; development data, tuned on, so optimistic); the before
column is the code at 52e8bc9 scored by the same scorer
(`results/prev_fix.json`, `results/new_fix.json`):

| Metric | Before (52e8bc9) | After |
|---|---|---|
| Precision / recall | 97.9% / 89.2% | 97.9% / 89.2% |
| Amount + currency (321 records) | 99.7% | 99.7% |
| Period + billing + basis | 86.6% | 86.6% |
| Product + plan | 54.5% | 78.2% |
| Full record | 49.5% | 69.2% |
| Plan field / product field | 65.4% / 79.1% | 87.9% / 85.0% |
| Pricing type | 82.9% | 83.5% |

The live development set (runs 6-7) could not be rescored: its raw stores
were lost with a deleted worktree. These numbers come from fixtures whose
rules were tuned while reading them, so a later section gives the held-out
results.

## Held-out pricing set (heldout-v1, scored 2026-10-22)

`benchmarks/market/pricing/heldout/` (build, provenance and agreement:
its `README.md`). The set has 32 pages, covering 7 page kinds, 18 industries
and 9 currencies (USD, EUR, GBP, INR, JPY, AUD, CAD, NZD, SGD). 22 are reduced
live snapshots from vendors and hosts never used in development; the other 10
are synthetic pages for hard layouts. The pages were frozen first.
Two AI labellers then labelled them independently, without seeing the
extractor, and a third adjudicated every disagreement. The result is 587
prices and 203 not-prices. The labels were committed (2398fc6) before any
scoring. The set was scored **once** for each code version: f7b0dce (this
milestone) and 52e8bc9 (before it, for the before/after). Nothing was changed
in response. Command: `python benchmarks/market/pricing/score.py heldout
--code <commit>` (PYTHONPATH at the code tree). Results:
`heldout/results/heldout-v1__<commit>.json`. No pass threshold is set: these
are measurements.

| Metric group | 52e8bc9 | f7b0dce |
|---|---|---|
| 1. Detection: precision / recall (587 labelled) | 84.4% / 48.9% (340 extracted, 287 matched) | 84.5% / 49.2% (342 extracted, 289 matched) |
| 2. Amount + currency (record) | 92.7% | 92.7% |
| 3. Period + billing + basis (record) | 59.3% | 57.1% (period 87.2, billed 83.6, basis 79.9) |
| 4. Product + plan (record) | 24.0% | 26.3% (plan 43.9, product 56.7) |
| 5. Full record | 16.0% | 17.3% |
| Exact unit (fields) | 53.0% | 50.9% |
| Unjudgeable | 3 fields (2 billed, 1 promotional), 0 records | same |

6. False positives (f7b0dce, 53):
   - 26 labelled not-prices of kind "other", such as illustrative or generic amounts and a bundled benefit's worth;
   - 16 competitor averages (comparison and review pages);
   - 4 quote records at an unlabelled amount;
   - 3 spend thresholds;
   - 1 each of discount, gift card, savings and an unlabelled "unknown".

7. False negatives (298), by layout and type:

   | Layout / type | Misses |
   |---|---|
   | Table / one-time | 67 |
   | List / usage | 50 |
   | Card / subscription | 29 |
   | Card / transaction | 19 |
   | List / transaction | 16 |
   | Inline / usage | 14 |
   | Table / transaction | 11 |
   | Card / one-time | 10 |
   | Table / subscription | 10 |
   | Table / usage | 9 |
   | Rest | 63 |

By page kind (f7b0dce):

| Page kind | Precision | Recall | Exact unit | Matched |
|---|---|---|---|---|
| Vendor pricing | 80.8% | 49.9% | 43.4% | 173 |
| Blog | 96.0% | 72.7% | 70.8% | 48 |
| Review | 87.5% | 36.8% | 39.3% | 28 |
| Shop | 94.4% | 51.5% | 70.6% | 17 |
| Comparison | 75.0% | 40.5% | 46.7% | 15 |
| Market report | 100% | 33.3% | 100% | 6 |
| Marketplace | 100% | 20.0% | 100% | 2 |

Attribution against the labels (f7b0dce, matched prices whose plan and
product could be judged):
- **Labelled "clear":** 61 right, 210 wrong.
- **Labelled "none":** 15 right, 2 wrong.
- **Labelled "ambiguous":** the single price is wrong.
- **By how the extractor found it:**

  | Found from | Right | Wrong |
  |---|---|---|
  | No plan or product found | 15 | 126 |
  | Clause | 22 | 33 |
  | Heading | 4 | 29 |
  | Table | 26 | 12 |
  | Structured data | 9 | 4 |

- **Marked uncertain:** 9, all counted wrong, because the label names the
  plan.

What this says:

* **The development scores do not generalise.** On fixtures this milestone
  took product+plan from 54.5% to 78.2%. On unseen pages it moved from 24.0%
  to 26.3%, and full records from 16.0% to 17.3%. Detection is the bigger
  gap: half the labelled prices are not found (recall 49% here against 89% on
  fixtures), mostly in tables of one-time prices (fees, tickets, licences),
  lists of usage rates, and cards.
* **Most attribution errors are prices with no plan or product at all** (126
  of 213 wrong). When a table or structured data gives the attribution, it is
  mostly right; a heading is mostly wrong.
* **Small regressions** in period/billing/basis (2.2 points) and exact unit
  (2.1 points) are in 4 blog records and 2 shop records. A period or basis is
  now read from nearby context the label does not count, for example
  "per month" on wise_zoom_sg and "/kg" on syn_grocery_in. They are reported,
  not tuned.
* **The amount and currency of what is found are reliable** (92.7%). Most
  false positives are other amounts on comparison and review pages
  (competitor averages, illustrative amounts).

Limits:

* **Label noise is high on these exact fields.** The labels come from AI
  agents and no human has reviewed them yet; `human_review_sample.json` is
  ready for 50 items. The two labellers agreed on every field for only 227
  of 581 paired prices. Product, plan, basis, inclusions and tax were the
  most disputed, so record scores on those fields include disagreement about
  conventions as well as extraction errors.
* **The set is small and weighted.** 32 pages, and 7 pages hold more than
  half the prices (instamojo 71, kbs_fees 57, magecomp_sms 46, fastmail 42,
  neworg 39, wise_zoom_sg 35, quicksprout_hosting 30).
  Per-kind rows with under 20 matches are anecdotes.
* **heldout-v1 is now development data.** Any change made because of these
  errors makes v1 development data; the next held-out claim needs
  `heldout-v2`.

## Recall work and heldout-v2 (2026-10-23)

heldout-v1 became development data. Its misses guided the recall changes in
7fbbdf0 (`market-rules-6`, text version `html-text-3`): landmark regions,
table cells with inner elements, price headings, pence, paise and Indian
grouping, and cue words. In parallel, a new set, heldout-v2, was built blind:

- 32 new pages (22 live, 10 synthetic), 21 industries, 13 currencies, 16
  countries;
- hosts disjoint from development and from v1;
- two AI labellers plus an adjudicator: 546 prices and 251 not-prices, with
  331 of 543 pairs identical on every field;
- frozen and committed (993bc3d) after the code and before any scoring.

Scored **once** per version, with no changes in response
(`heldout_v2/results/`):

| Metric | v1, development (f7b0dce -> 7fbbdf0) | **v2, held out: f7b0dce** | **v2, held out: 7fbbdf0** |
|---|---|---|---|
| Precision | 84.5% -> 88.6% | 82.7% | 82.7% |
| Recall (539 judgeable) | 49.2% -> 83.6% | 52.3% | 54.2% |
| Amount + currency | 92.7% -> 83.7%\* | 95.4% | 95.9% |
| Period + billing + basis | 57.1% -> 61.6% | 74.5% | 75.3% |
| Product + plan | 26.3% -> 23.8% | 11.3% | 13.4% |
| Full record | 17.3% -> 16.3% | 8.5% | 10.3% |

\* v1's labels give a bare "$" on Australian, NZ and Japanese pages a
currency, where the code leaves it unsettled ("Known limitations").

On v2 (7fbbdf0):
- **False positives (61):** 34 amounts not labelled at all (15 unknown, 7
  quote, 6 subscription, 4 usage, 2 one-time), and 27 labelled not-prices (18
  "other", 4 savings, 4 spend thresholds, 1 competitor average).
- **False negatives (247):**

  | Layout / type | Misses |
  |---|---|
  | Card / subscription | 29 |
  | JSON-LD / subscription | 25 |
  | Card / one-time | 23 |
  | Inline / subscription | 19 |
  | Table / one-time | 17 |
  | Inline / transaction | 17 |
  | JSON-LD / one-time | 14 |
  | Table / subscription | 13 |

- **Where the misses are:** half the labelled prices sit on 9 pages, and 6
  pages are missed entirely: syn2_shipping_se, somenergia, locaweb,
  syn2_accounting_pl, syn2_insurance_de, smartfit_mx.

What this says:

* **The v1 recall gain mostly does not carry over.** It was +34 points on
  the set it was tuned on and +1.9 points on unseen pages. The landmark,
  cell and heading fixes are general but rare on v2; most v1 misses were
  specific to a few large v1 pages.
* **The next bottleneck is locale number formats and currencies.** I checked
  this on made-up strings, not on v2 pages, so v2 stays clean:

  | Text | Read as |
  |---|---|
  | "R$ 29,90" | 29 |
  | "$ 1.299,00 MXN" | 1.299 USD |
  | "29,90 €" | no price |
  | "149 kr", "49 zł", "R 199", "COP 49.900" | no price |

  These are wrong amounts as well as misses. The pages missed entirely are
  in Sweden, Spain, Brazil, Poland, Germany and Mexico. The numeric gate's
  own re-read catches one of the wrong amounts, because it reads "29,90" as
  2990, not 29. It **does not** catch "1.299,00": the gate reads 1.299 too,
  so that wrong amount would pass. This is a gap in a quality gate, to fix
  together with the parser.
* **Attribution is the weakest part on unseen pages** (product+plan 13%).
  The labels name plans and products as each page writes them at each
  occurrence; the rules often leave them empty or take a heading or table
  label that is not the label's name. Plan and product were also the
  fields the labellers disputed most (95 and 72 of 543 pairs).

Limits are as for v1: AI labels, no human review yet
(`heldout_v2/human_review_sample.json`), and 32 pages. **Any change made in
response to v2 makes it development data; the next claim needs a
heldout-v3.**

## Long PDFs (page targeting, 2026-10-22)

The real check is one 59-page research report: gfi.org's plant-based meat
report, a local copy used offline and not committed. The case was "plant-based
meat", United States, with all 8 industry questions open. Script:
`pdfcheck.py` (job scratch). The test suite was running at the same time, so
times are inflated.

| Page grant | Mode | Pages read (last page) | Excerpts | Questions with evidence | Time |
|---|---|---|---|---|---|
| 10 | first pages (before) | 10 (p10) | 26 | 7/8 | 7 s |
| 10 | targeted, `outline + text` | 10 (p52) | 40 | 7/8 | 26 s |
| 50 | first pages (before) | 50 (p50) | 40 | 7/8 | 22 s |
| 50 | targeted, `outline + text` | 50 (p58) | 40 | 7/8 | 27 s |

Within a small grant, targeting reads into the back half of the report and
keeps more excerpts. With a large grant, the per-source cap of 40 excerpts
binds, not the page choice. Neither mode answers `definition`, because the
report states no definition the rules recognise. The text pass costs time:
it is stopped at 40% of the extraction time limit, and pages it did not reach
rank by section title only. The first version (outline sections only) read 7
of the 10 pages for 3 questions and missed the market-size figure. This
check led to round-robin selection and market-figure weighting. It is one
document, so it is development evidence, not a measured rate.

Run 7's miss (the 84-page RVO "Proteins Study", which answers the
business-models question only on pages 54-55, past the 50-page grant) is
reproduced in `tests/test_market_pdf_targeting.py` with a synthetic 84-page
document. A full research run reads pages 54-55 within an 8-page grant, and
their quotes verify on re-extraction (`test_a_run_reads_pages_54_55_...`). No
page number is in the code: the pages' text, or an outline or contents entry,
points to them. The live RVO document itself is unreachable from here
(above).

## Acceptance criteria

| Area | Criterion | Kind | Basis |
|---|---|---|---|
| Evidence provenance | provenance gate: 0 failures | deterministic | invariant (baseline 0 failures / 479 excerpts in run 3) |
| Claim-to-source traceability | citations gate: 0 dangling ids | deterministic | invariant |
| Numeric extraction | numeric gate: 0 failures; analyst value + currency 100% | deterministic + analyst | baseline 100% in all runs |
| Currency and unit normalization | numeric gate (currency re-derived from the text); sizing refuses other currencies and non-annual prices | deterministic | tests + baseline |
| Market-scope consistency | scope gate: 0 failures; analyst: every evidence-derived total of the requested market | deterministic + analyst | baseline 2/2 in run 3 |
| Sizing calculation | sizing_math gate: 0 failures; hand-computed case values match | deterministic | invariant |
| Unsupported claims | unsupported gate: 0 failures | deterministic | invariant |
| Partial-run reporting | status gate: 0 failures | deterministic | invariant |
| Figure accuracy (kind and scope) | no regression below the run-3 baseline on its labelled set (49/58, 84%); review every change of a case's selected total | analyst | baseline; no fixed rate is claimed beyond it |
| Price extraction | fixture precision >= 97%, recall >= 88%, exact unit >= 85% (`test_pricing_fixture_benchmark_holds`); live set scored on change and compared with this section | deterministic floor + reported | 2026-10-21 scores above; floors, not measured error rates |
| Price extraction on unseen pages | heldout-v1 scored once per code version, all seven metric groups reported; no threshold | reported | 2026-10-22: precision 84.5%, recall 49.2%, full record 17.3% ("Held-out pricing set"); a new held-out version is needed before the next claim |
| Source coverage | flagged (not failed) when under half the research questions are answered or retrieval rate < 0.5 | reported | baseline: the plant-based case (1/8, 0.27) is the one whose report is not usable; the others answer 6-8 of 7-8 at 0.50-0.71 |

A run is "successful" only in the sense of its status: COMPLETED requires
every research question answered with no failed source and no warning. All
five live cases are PARTIAL, with their reasons in `stats.status_reasons`;
that is the honest result for small budgets on the open web, not a failure
of the gates.

The figure-accuracy and coverage thresholds come from five cases and about
60 labelled figures: they are a baseline to detect regressions, not a
measured error rate of the engine. Grow the labelled set before quoting a rate.
