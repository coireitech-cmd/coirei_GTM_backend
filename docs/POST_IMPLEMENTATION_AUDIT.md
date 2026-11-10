# Post-implementation audit: market intelligence engine

Date: 2026-10-21. Scope: the uncommitted `feat/market-engine` work
(`gtm_crawler/market/`, migration 0016, worker/API/config hooks, docs),
audited in worktree `.claude/worktrees/market-audit` (branch
`worktree-market-audit`, base `main` f08e89f). Every finding below was
reproduced by running code; none rests on reading docs alone.

## Baseline (before any fix)

| Check | Command | Result |
|---|---|---|
| Full suite, PostgreSQL 16 up | `python -m pytest -q` | 996 passed, 9 skipped (198 s) |
| Lint | `ruff check .` | clean |
| Format | `ruff format --check .` | 169 files formatted |
| Types | `mypy` (strict) | no issues in 74 files |

Probes: `probe.py` / `probe2.py` (session scratch, not committed) ran each
suspected defect against the code; the outputs are quoted per finding.

## What exists

| Capability | State |
|---|---|
| Request/report models, plan, guarded single-page collection, rule extraction, assessment, contradictions | Implemented, unit- and fixture-tested |
| Industry and product analysis, sizing, scoring, Markdown export | Implemented, fixture-tested |
| Store, worker integration, API, CLI | Implemented, tested against PostgreSQL |
| Cache fallback | Implemented, tested |
| OCR | Implemented; tested with a fake reader only (Tesseract not installed) |
| Optional model narrative and `/ask` answers | Implemented; tested with a mocked endpoint only |
| PDFs, table figures, JS-rendered pages, currency conversion | Missing (documented limitations) |

No `TODO`/`FIXME`/`NotImplementedError`, no hardcoded market statistics or
source URLs, no mock provider reachable from production code (without
`GTM__SEARCH__API_KEY` the run reports "no search provider configured").
Every broad `except` records an issue or a source status. Competitor
analysis (`pipeline`, `dossier`, crawl jobs) does not import `market`; the
only shared change is that a worker runs a market run when no crawl job is
queued.

## Findings

Status is the state after the fix pass (see "Fixes").

### High

**H1. Bottom-up TAM with a monthly price is 12x too low and labelled "per year".**
`sizing.bottom_up`. Probe: customers 1,000 x 1 seat x `10 USD/seat/month`
-> `TAM ... = USD 10,000.00 per year` (true annual figure 120,000). Only the
currency prefix of `price_per_unit.unit` was checked. Expected: an annual
price, or a refusal. Fix: refuse a price whose unit names a period other
than a year (month, week, day, quarter, hour) with a message to convert it;
a unit without a period is warned as "assumed per year".

**H2. Foreign-dollar and word-internal currency tokens parsed as USD / INR.**
`extract._MONEY` / `parse_metrics`. Probe: "HK$5 billion" -> `USD 5e9`;
"S$3 billion" -> `USD 3e9`; "20 hou**rs 5** million times" -> `INR 5e6`. The
`$` and `Rs` alternatives had no left boundary. Consequences: wrong-currency
figures compared with USD figures (false contradictions) and usable as a USD
top-down total. Fix: a currency token may not follow a letter.

**H3. Runs that collected no evidence finished `COMPLETED`.**
`engine.run_research` status rules. Probe: search returns only an off-topic
page -> `completed 0 []`; search returns nothing -> `completed 0 []`. An
empty report looked like a successful run. Fix: no evidence -> an issue
naming what was tried, status `FAILED` (unless cancelled).

### Medium

**M1. Model-written sentences could state numbers the cited excerpts do not.**
`llm.validated`. Probe: "The market was USD 50 billion in 2023
[ev_0123456789ab]." kept with a valid id although nothing checks the 50.
Fix: a sentence is kept only if each number in it appears in the excerpts
it cites.

**M2. `market.llm_base_url` localhost exception was a prefix match.**
`config.MarketSettings.__post_init__`. Probe: `http://localhost.evil.example/v1`
and `http://127.0.0.1.nip.io/v1` accepted: evidence and the API key would go
in cleartext to a remote host. Fix: parse the URL; plain http only when the
host is exactly `localhost`, `127.0.0.1` or `::1`.

**M3. A "sourced" sizing input could cite evidence ids that do not exist.**
`sizing._result` via `engine.run_research`. Probe: `evidence_ids:
["ev_fake00000000"]` -> basis `sourced`, only "not independently
corroborated". Fix: in a research run, inputs citing ids not in the run get
"cites evidence not collected in this run" and cannot be verified.

**M4. Go-to-market hypothesis mixed currencies and price units.**
`analyze.go_to_market`. Probe: INR 999/month, INR 1,499/month, USD 15/user
-> "median of 999 per user/month ... sales-assisted". Fix: monthly prices
only (`/month`, `/mo`), in their most common currency, currency named.

**M5. An unreadable cached report failed the whole run.**
`engine.run_research` (`prior`). Probe: `prior` raising `ValidationError`
-> `run_research` raised. The cache is optional input like the dossier. Fix:
a failing cache lookup is a warning issue.

**M6. Cancellation and the duration budget were not checked during discovery.**
`engine._discover`. Probe: a run already cancelled sent all 8 paid search
queries. Fix: discovery stops at `stop` or the deadline; queries not sent
are counted and reported.

**M8. "2581.22 USD Million" not parsed; "grow from X" filed as a forecast.**
Found in the audit's live product run (`extract._MONEY`, `parse_metrics`).
The amount-currency-scale order matched as a bare USD 2,581 and was dropped
as too small for a market figure; the base value in "projected to grow from
X in 2025 to Y by 2035" was a forecast because "projected" precedes it, so
sizing never used it. Fix: an optional scale after the trailing currency;
a figure right after "from" is the base value. Re-run live: both figures
parsed (USD 2.58B 2025 size, USD 7.67B 2035 forecast).

**M7. A source's own sub-figures widen a metric's range (fixed in the second
milestone, see "Milestone 2" below).**
`assessment.metric_summaries`, `sizing.evidence_total`. Probe: one page,
"global ... USD 70 billion in 2023" and "North America ... USD 30 billion
... in 2023" -> one summary `30-70 billion`, and an evidence-derived TAM
base of 50 billion (LOW confidence, unverified).

### Low

**L1.** Docs said the evidence-derived TAM base is the figures' median; the
code uses the midpoint of their range. Fixed (docs).

**L2.** Scoring ratings for unknown dimensions were silently ignored
(`scoring.score_opportunities`). Fixed: reported like unknown weights.

**L3.** No test showed that a queued crawl job is claimed before a market
run (`worker.run_worker`). Fixed: regression test added.

**L4 (open).** OCR image downloads (`ocr.Ocr.read`) are not inside the
per-host throttle slot. Bounded by `ocr_max_images_per_page` (4) and off by
default.

**L5 (open).** `llm.validated` splits sentences at ". "; a model writing
"Text. [ev_x]" loses the sentence (fails closed, not open).

**L6 (fixed in milestone 2).** Known-competitor pages are not counted against
`max_sources` (up to 4 URLs x 20 competitors).

**L7 (fixed in milestone 2).** `subject_key` for product mode ignores the
product details; a cache fallback may reuse evidence gathered for a different
product input of the same name and scope.

## Fixes

| Finding | Change | Regression test (`tests/`) |
|---|---|---|
| H1 | `sizing._annual_price`: non-annual price refused, period-less warned | `test_bottom_up_refuses_a_monthly_price` |
| H2 | `extract._MONEY`: no currency token after a letter | `test_currency_tokens_after_letters_are_not_parsed`, `test_currency_tokens_still_parse_after_spaces_and_punctuation` |
| H3 | `engine.run_research`: no evidence -> error issue -> `FAILED` | `test_a_run_without_evidence_is_failed` |
| M1 | `llm.validated`: numbers must appear in the cited excerpts | `test_model_sentences_must_use_the_cited_numbers`; `test_ask_with_a_model_keeps_only_cited_sentences` (now cites the excerpt that states the figure and checks a wrong figure is dropped) |
| M2 | `config.MarketSettings`: exact host check with `urlsplit` | `test_llm_base_url_plain_http_only_to_this_machine` |
| M3 | `sizing.size_market(..., known)`: unknown evidence ids warned, never verified | `test_sourced_sizing_inputs_must_cite_run_evidence` |
| M4 | `analyze.go_to_market`: monthly prices in one currency; price units keep the period (`USD/user/month`) | `test_gtm_hypothesis_uses_monthly_prices_in_one_currency`; `test_metric_kinds` updated |
| M5 | `engine.run_research`: cache lookup failure is a warning | `test_unreadable_cache_is_an_issue_not_a_crash` |
| M6 | `engine._discover`: stops at cancellation or deadline | `test_cancelled_or_out_of_time_runs_send_no_further_queries` |
| M8 | `extract._MONEY` trailing scale; "from" = base value | `test_amount_currency_scale_order_is_parsed` |
| L1 | Docs and docstring: midpoint, not median | - |
| L2 | `scoring.score_opportunities`: unknown rating dimensions refused | `test_ratings_for_unknown_dimensions_are_refused` |
| L3 | - | `test_worker_runs_a_waiting_crawl_job_before_a_market_run` |

`EXTRACTOR_VERSION` is now `market-rules-2` (price units and currency
parsing changed). Report `SCHEMA_VERSION` is unchanged (no field changed).

## Workflow verification

| Workflow | How | Result |
|---|---|---|
| A. Competitor analysis (crawl, extraction, dossier, API, worker) | `test_pipeline`, `test_acceptance_phase18`, `test_api`, `test_jobs`, `test_extract_competitor`, `test_dossier_clean`, `test_external`, and the new crawl-before-market worker test | 65 passed; full suite green |
| B. Industry analysis | `test_industry_run_end_to_end`, `test_industry_report_integrity` (fixture web: blocked, failed, duplicate, irrelevant and off-topic sources, one failing query) | PARTIAL with each failure recorded; contradiction kept with both values |
| C. Product analysis | `test_product_run_with_competitors_and_dossier`, `test_product_report_separates_facts_interpretations_and_unknowns` | direct / derived / user / inferred findings all present and labelled; missing problem, customer and price listed as gaps; differentiator "absence of evidence, not uniqueness" |
| D. Sizing | `test_independent_check_of_bottom_up_scenarios_and_sensitivity` (hand-computed TAM/SAM/SOM, low/high, sensitivity swings), existing top-down, cross-check, unit and double-count tests, H1 test | all match |
| E. Evidence integrity | `_assert_integrity` in B and C: every id cited by findings, metrics, contradictions, sizing and scoring exists; every xpath excerpt re-derived from its stored raw page; each parsed figure's text is inside its excerpt; summary bounds are stated figures; user and hypothesis findings LOW | pass |

Live smoke tests (Exa search, real sites; separate from the fixture tests;
`gtm-market`, small budgets; outputs in session scratch, not committed):

| Run | Result |
|---|---|
| Industry "CRM software", India, 3 queries / 5 sources | PARTIAL; 4 fetched, 2 x HTTP 403, 9 PDFs skipped; 138 excerpts; 138/138 quotes re-derived from raw pages; 0 dangling ids; evidence TAM USD 2.5B (2025) marked not verified |
| Product "LeadBook", Zoho CRM known competitor, 3 queries / 4 sources | PARTIAL; 7 fetched, 3 x 403; 104 excerpts; 104/104 quotes re-derived; 0 dangling ids; no price points (Zoho pricing is JavaScript-rendered: known limitation); exposed M8 |
| Same product run after the M8 fix | PARTIAL; 104/104 quotes; USD 2.58B (2025) and USD 7.67B (2035) now parsed |

Not runtime-tested: OCR with real Tesseract; the model narrative and `/ask`
answers against a real model endpoint; the worker and API against live
search (tested with mocked search transports only).

## Verification (after the fixes)

| Check | Result |
|---|---|
| `python -m pytest -q` (PostgreSQL 16 up) | 1018 passed, 9 skipped (205 s) |
| `pytest tests/test_market.py tests/test_market_service.py` | 74 passed |
| `ruff check .`, `ruff format --check .` | clean, 170 files formatted |
| `mypy` (strict) | no issues in 74 files |
| `import gtm_crawler.api, gtm_crawler.worker, gtm_crawler.market.runner`; `gtm-market --help` | ok |

The 9 skips are pre-existing and none is a market test: 7 load scenarios
(`GTM_LOAD=1`), the sandbox-image test (`GTM_SANDBOX_IMAGE`) and the S3
raw-store test (`GTM_TEST_S3_ENDPOINT`).

## Milestone 2 (2026-10-21): market scope, benchmark, quality gates

### M7 fixed: market-scope contamination

Trace before the fix: `extract.parse_metrics` -> `Metric(kind, value, unit,
year)`; the record's `geography` = the requested geography if the excerpt
named it -> `assessment._metric_groups` keyed (kind, unit, year, that
geography) -> one `MetricSummary` low/high over every figure in the group ->
`sizing.evidence_total` midpoint. A page's global total and regional figure
landed in one group.

Now (`market/scope.py`), each market figure carries the scope its own clause
states, read from its sentence only: geography (gazetteer of regions and
major countries, case-sensitive abbreviations, the requested place; several
places kept joined, never one picked; nothing inferred from the page title),
segment (customer size, deployment, vertical qualifiers of the market noun,
or named right after it), measure (market value, spending, investment, or a
revenue that names no market), the market phrase, whether it names another
market, a qualifier when the phrase adds words beyond the subject's
vocabulary ("CRM marketing services"), and whether it is a part of a larger
figure. A clause naming nothing continues the previous figure of the same
sentence (basis `carried`).

* Summaries and contradictions group by the full scope; company revenue is
  no market metric. One source stating several values for one scope is
  `ambiguous` (LOW); a part, another market or a qualified market is LOW; an
  unstated geography caps confidence at MEDIUM. Independent sources of the
  same scope still corroborate or contradict each other.
* The evidence-derived total uses only figures of the target scope
  (requested geography, Global if none; requested segment, whole market if
  none; currency; latest year not after the sizing year; measure market; no
  part, other market or qualifier outside the sizing's market definition).
  Every other figure is in `sizing.excluded` with its reason. A figure whose
  geography is not stated is used only when no geography was requested and
  none states Global, and the total is then unverified. No currency or year
  conversion is made, so neither can merge scopes.
* Cached evidence is re-read from its excerpts with the current rules
  (reports of schema 1 get scopes too).

Tests: the ten requested scenario classes (`test_scope_*`), clause-reading
cases, an end-to-end run over a contaminated page, and the live sentences
the benchmark exposed (`test_live_*`).

### Benchmark and quality gates

`docs/MARKET_BENCHMARK.md`: 5 live cases (2 industry, 2 product, 1 sizing
with a source-checked input), deterministic gates (provenance, citations,
numeric re-parse, scope, sizing math, unsupported claims, status),
analyst labels, baseline and thresholds. All gates passed in all three runs
and through the API + worker; analyst-labelled figure accuracy went from
31/64 to 49/58 as the defects the review found were fixed.

Status now reflects coverage: research questions without evidence and a
requested sizing that could not be calculated make a run PARTIAL;
`stats.coverage` and `stats.status_reasons` say why. The COMPLETED path is
tested (it was not before).

### Remaining limitations, ranked by impact on research correctness

| Rank | Gap | Impact | Decision |
|---|---|---|---|
| 1 | Product-name cache contamination | wrong evidence silently attributed to another product | **fixed**: `subject_key` includes industry and a hash of the product input (stored runs with old keys are simply not reused) |
| 2 | JavaScript-rendered pricing | product pricing section empty for major competitors (Zoho, seen live) | **fixed** for competitor pages: script shells and pricing pages stating no price are rendered with the crawler's guarded browser fallback (at most 8 per run; same network guard, robots.txt and throttle); tested with real Chromium. Not triggered in the benchmark (its competitors' pricing HTML states prices) |
| 3 | PDF extraction | official statistics are PDFs: 9 PDFs skipped in one earlier live run; the plant-based case lacked official data | **not done**: needs a new dependency, a PDF locator type re-derivable like XPath quotes, and parser hardening against untrusted files. Next task |
| 4 | Competitor pages outside the source budget | cost and latency, up to 80 extra fetches | **fixed**: per competitor `max_pages_per_domain`, all together `max(max_sources, max_pages_per_domain)`, the rest SKIPPED with the reason; `/pricing/` no longer also tried as `/pricing/pricing` |
| 5 | Live worker/API integration | untested path | **verified**: two benchmark cases through `POST /v1/market/research`, `gtm-worker` and `GET .../report` against a fresh migrated database with live search; same gate results as in-process |
| 6 | Real-model narrative and `/ask` | optional, off by default; citations and numbers are already validated against excerpts | **blocked**: no model endpoint or key configured here |
| 7 | OCR | optional, off by default | **blocked**: Tesseract is not installed here |

### Milestone 2 verification

| Check | Result |
|---|---|
| `python -m pytest -q` (PostgreSQL 16 up, nothing else using the test database) | 1069 passed, 9 skipped (198 s) |
| `pytest tests/test_market.py tests/test_market_service.py tests/test_market_benchmark.py` | 125 passed |
| Competitor-analysis suites (`test_pipeline`, `test_acceptance_phase18`, `test_api`, `test_jobs`, `test_extract_competitor`, `test_dossier_clean`, `test_external`, `test_browser`) | 73 passed |
| `ruff check .`, `ruff format --check .`, `mypy` (strict) | clean (174 files formatted, 76 source files typed) |
| Live benchmark, 3 runs x 5 cases + 2 cases via API and worker | all 7 gates pass in every case; details in `docs/MARKET_BENCHMARK.md` |

One full-suite attempt failed 2 crawler tests with a PostgreSQL deadlock and a
pre-existing job: I had started the market tests (whose fixtures migrate and
downgrade the same test database) while it ran. Both tests pass alone, and
the rerun with nothing else on the database passed in full.

## Milestone 3 (2026-10-21): PDF evidence

What was traced first: an HTML quote is `Locator(xpath, block, offsets)`
into a block of the page parsed from the stored raw bytes
(`SourceRecord.raw_sha256`); `evidence.resolve` re-derives it, and the
benchmark's provenance gate did exactly that. PDFs were skipped by URL
(`engine._DOCUMENT`).

Implemented (design and formats: `docs/MARKET_ENGINE_IMPLEMENTATION.md`,
"PDF evidence"): signature-based detection in the collector; the `[pdf]`
extra (pdfplumber 0.11.10 / pdfminer.six 20260107); extraction in a killable
child process with size, page, character, time and (POSIX) memory/CPU
limits; per-page normalized text with block offsets, ruled tables, caption
units, metadata and versions, stored next to the file and bound to its
hash; `Locator(type=pdf, page, offsets, version, table, row)`; table-row
figures under the M7 scope rules; `verify.py` with `verified_reextracted`,
`verified_stored`, `mismatch`, `not_verifiable`, `source_changed`; the
benchmark's provenance gate on `verify_report` and a numeric check of table
cells; page numbers in report citations and `/ask`; `RetrievalStatus.UNREADABLE`.

Found and fixed during the milestone (each with a regression test):

| Found by | Defect | Fix |
|---|---|---|
| probe | pdfplumber wraps the password error: encrypted files were "malformed" | the exception chain is searched |
| probe | two text columns were read line by line across both | words grouped into column segments, read column by column |
| live run 4 | every real PDF failed on Windows: the child wrote JSON in cp1252 | UTF-8 bytes on stdout |
| live run 5 | prose in table cells made a 2,000+ char row; its validation error ended the run | long rows skipped; per-source extraction guarded |
| live run 5 | chart labels became prices: "plan" matched "Plant-based" | whole-word price rule |
| this session | installing Chromium for the new venv's Playwright 1.64 removed build 1243 that the main `.venv` (Playwright 1.63) uses: its browser fallback broke | reinstalled with the main venv's Playwright; both builds present, browser tests pass in both venvs |

Remaining (also in the implementation doc): no PDF OCR (scans reported,
not read); unruled tables give no figures; no memory limit for the child on
Windows; PDFs share the source budget and can displace pages (seen live:
the EV case lost its only eligible total); chart text is read as label
runs; per-unit prices like "/lb" keep no unit.

### Milestone 3 verification

| Check | Result |
|---|---|
| `python -m pytest -q` (worktree `.venv` with `.[dev,pdf]`, PostgreSQL 16) | 1103 passed, 9 skipped |
| `pytest tests/test_market_pdf.py tests/test_market_pdf_quality.py` | 34 passed |
| `test_market`, `test_market_pdf*`, `test_market_benchmark`, `test_browser` without pdfplumber (main `.venv`) | 127 passed, 34 skipped (exactly the 34 PDF tests) |
| `ruff check .`, `ruff format --check .`, `mypy` (strict) | clean |
| Live benchmark runs 4-6 (`docs/MARKET_BENCHMARK.md`) | run 6: all gates pass; every PDF quote `verified_reextracted` |

## Milestone 4 (2026-10-21): source selection, budgets, coverage

Root cause of the selection defect (traced in `run6` vs `run3`): a fixed
order by search rank + 3 x (tier - 1), decided before collection; search
titles and dates discarded; every fetch, irrelevant or empty included,
counted against `max_sources`. In the EV case 4 of 6 slots went to tier-1
pages and a PDF, 3 of them without evidence, and the page with the only
eligible total was never fetched. Details: `docs/MARKET_BENCHMARK.md`,
"Source selection".

Implemented: `market/select.py` (candidates with search metadata; value =
need fit x quality; greedy diverse waves) and `engine._collect` (waves
processed in pick order; explicit budgets for useful sources, attempts,
HTML pages, PDFs, PDF pages per document and per run, bytes, per domain;
selection rationale on every source; per-question coverage and reasons;
budget-stop warning). Design: `docs/MARKET_ENGINE_IMPLEMENTATION.md`,
"Source selection and budgets".

An independent review of the change (a separate agent, read-only) found 10
defects, all fixed with regression tests except the byte overshoot
(documented): PDF page budget overrun within a wave; PDFs behind non-.pdf
URLs bypassing `max_pdfs`; cancellation not reaching in-flight fetches; the
PDF child surviving cancellation; `stop` ignored mid-wave and for competitor
pages; competitor pages lost on an early stop; a deadline-cut final wave
labelled "every candidate was considered"; a stop message quoting a
budget-blocked candidate's value; stale selections on untried candidates;
robots refusals counted as attempts. The first scenario run found two more:
the HTML allowance defaulted to `max_sources` and blocked recovery after a
failure; pages every query returns won on breadth (fit now = best question
+ 0.05 of each other).

Result (live run 7 vs run 6, same cases and budgets): the EV total is
recovered (USD 13.8 B, Europe 2024); questions answered 34/38 -> 35/38;
attempts without evidence 29 -> 20; blind-labelled figures 38/57 -> 42/60,
market figures 6/6 -> 20/23, evidence totals 1/1 -> 2/2. One regression,
caused by the new default `max_pdf_pages` = 50: the plant-based case read
its 84-page PDF to page 50 and lost the question answered on pages 54-55.

### Milestone 4 verification

| Check | Result |
|---|---|
| `python -m pytest -q` (worktree `.venv`, PostgreSQL 16) | 1117 passed, 9 skipped |
| `pytest tests/test_market_selection.py` | 15 passed |
| `ruff check .`, `ruff format --check .`, `mypy` (strict) | clean |
| Live run 7 (`results/2026-10-21/run7-selection.json`) | all 7 gates pass in every case |
