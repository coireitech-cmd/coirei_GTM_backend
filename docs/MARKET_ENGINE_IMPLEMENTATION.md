# Market intelligence engine

Industry- and product-level market research on top of the crawler
(`gtm_crawler/market/`). This document is the architecture, the methods,
and the implementation state: a later session can resume from it without
repeating the audit (`docs/ARCHITECTURE_ASSESSMENT.md`).

Related: audit and defect list `docs/POST_IMPLEMENTATION_AUDIT.md`; data model `docs/MARKET_DATA_MODEL.md`; sizing
`docs/MARKET_SIZING_METHODOLOGY.md`; security `docs/security-review.md`
("Market research").

## Architecture

```
POST /v1/market/research ──> market_research (queued) ──> gtm-worker claims (lease)
gtm-market request.json ───────────────┐                        │
                                       v                        v
                       runner.research(): providers ──> engine.run_research()
                                                              │
   plan.build_plan ─> search (Exa via search.FailoverSearch) ─> _select (tier, rank, caps)
     ─> collect.Collector (PolicyEngine/robots ─> HostThrottle ─> HttpFetcher+NetGuard ─> raw store ─> parse_page)
     ─> extract.extract_evidence (+ ocr.Ocr, + engine.dossier_evidence from dossier.build_dossier)
     ─> assessment (classify, source_quality, find_contradictions, metric_summaries, confidence)
     ─> analyze (industry_sections | product_sections, gaps, executive_summary)
     ─> sizing.size_market   ─> scoring.score_opportunities   ─> [llm.ChatModel.narrate]
     ─> models.Report ──> store.finish / report.to_markdown / API
```

| Module | Responsibility | Depends on |
|---|---|---|
| `models.py` | Validated, versioned schemas (pydantic) | - |
| `plan.py` | Research questions -> queries, round-robin, query budget | models |
| `collect.py` | One page per URL under robots, guard, throttle, size and time limits | crawler fetch/parse/robots/policy/throttle/rawstore |
| `extract.py` | Rule-based evidence and metric parsing, quoted with locators | `evidence.quote_block`, parse |
| `ocr.py` | Optional image OCR (ported from competitor_analy2) | Pillow, pytesseract (optional) |
| `assessment.py` | Source class/tier, source quality, claim confidence, contradictions, metric summaries | `evidence.classify_source` |
| `analyze.py` | Report sections for industry / product, gaps, executive summary | assessment |
| `sizing.py` | TAM/SAM/SOM, scenarios, sensitivity, verification flag | models |
| `scoring.py` | Configurable opportunity scores, explainable, sensitivity | assessment |
| `llm.py` | TF-IDF retrieval over evidence; optional OpenAI-compatible model with citation validation | httpx |
| `report.py` | Markdown export | models |
| `engine.py` | Orchestration, budgets, cancellation, partial failure, cache fallback | all above (injected providers) |
| `store.py` | `market_research` rows: create, lease, heartbeat, cancel, finish, latest report | SQLAlchemy |
| `ads.py` | Competitor ads: manual import (`ad`, `ad_observation`), ad evidence, `competitor_ads` section, performance-claim check (docs/ad-intelligence-design.md) | db models, extract, assessment |
| `runner.py` | Worker processing and `gtm-market` CLI; wires providers from config | engine, store, search, dossier, ads |
| `api.py` | `/v1/market/...` routes (mounted by `api.create_app`) | store, report, sizing |

Boundaries: analysis never imports the API, store or runner; the engine
gets search, dossier, cache and narrator as callables, so every stage runs
without a network, database or paid API in tests. Competitor analysis
(`pipeline`, `dossier`) is unchanged and does not depend on `market`.

## Research workflow

1. **Validate** (`ResearchRequest`): mode, subject, scope, currency, budget
   (from `depth` unless given), product input for product mode.
2. **Plan** (`plan.py`): ordered questions with the dimensions that answer
   them; their queries round-robin so each question gets a first query
   before any gets a second; beyond `max_queries` recorded as not sent.
   Industry: size, official statistics (search restricted to
   `market.statistics_domains`), definition, demand, competition, external
   factors, risks, business models. Product: demand, alternatives, category
   size, pricing, workarounds, go-to-market, barriers.
3. **Discover**: each query through the configured provider. A failed query
   is an issue; the run continues. Cancellation and `max_duration_seconds`
   stop discovery too; unsent queries are counted (`stats.queries_not_sent`).
4. **Screen candidates**: one candidate per URL with what search said
   about it (title, date, best position) and the queries and plan questions
   that found it. Office documents and archives are not fetched; PDFs are,
   when `market.pdf_enabled` and the `[pdf]` extra is installed (else
   SKIPPED with that reason).
5. **Collect** known competitor pages first (not counted in the budgets),
   then search results in waves chosen by the questions still open ("Source
   selection and budgets" below). robots.txt (`BLOCKED`), guarded fetch
   (`FAILED`, `BLOCKED`), raw body stored, parsed. Links are never followed.
   A body with the `%PDF-` signature is a PDF whatever its URL or declared
   type ("PDF evidence" below); a non-HTML body without it is `UNREADABLE`.
   Each wave is processed (steps 6-8) before the next is chosen.
6. **Deduplicate**: same main-text hash as an earlier-ranked source ->
   `DUPLICATE` (deterministic: decided in selection order after collection).
7. **Relevance**: a non-vendor page mentioning under half of the subject
   terms (and fewer than three) -> `IRRELEVANT`.
8. **Extract** (see below), plus OCR text and dossier facts when available.
9. **Cache fallback**: if nothing new was fetched and an earlier finished
   report of the same `subject_key` exists, its direct evidence is reused,
   marked `CACHED` / `cached_from`. An earlier report that cannot be read
   is a warning, not a failed run.
10. **Assess**: source quality per source; contradictions; metric summaries.
11. **Analyze**: sections, open questions, executive summary.
12. **Size** (if `sizing` given) and **score** (subject plus `segments`).
13. **Narrate** (optional model, `max_llm_calls >= 1`).
14. **Status**: `CANCELLED` if stopped; `FAILED` if no evidence was
    collected (an error issue lists the source statuses); `PARTIAL` if any
    warning or a skipped/failed source, a research question without
    evidence, or a requested sizing that could not be calculated; else
    `COMPLETED`. `stats.coverage` (questions answered, sources tried/used,
    domains, evidence) and `stats.status_reasons` (every reason the run is not
    COMPLETED) are on every report. A report alone is never success.

Budgets (`Budget`, all validated with hard maxima; selection budgets
below): `max_queries`, `results_per_query`, `max_sources`, `max_attempts`,
`max_html_pages`, `max_pdfs`, `max_pdf_pages`, `max_extraction_pages`,
`max_total_bytes`, `max_pages_per_domain`,
`request_timeout_seconds` (also caps the fetcher's per-attempt timeouts;
retries capped at 2), `max_content_bytes` (caps the fetcher's body limit),
`max_duration_seconds` (collection stops; analysis runs over what was
collected), `max_llm_calls`. Crawl depth is fixed at 0 for open-web pages
(policy).

## Evidence extraction

`extract.extract_evidence`: blocks outside nav/header/footer/aside and not
headings; sentences of 40-600 characters; cookie/newsletter/sample-request
furniture dropped; repeated sentences once. A sentence is evidence when it
matches a dimension rule (17 dimensions: market size, growth, definition,
demand driver, pain point, adoption, willingness to pay, segment,
competition, pricing, substitute, barrier, regulation, technology, trend,
risk, distribution) or states a parsable figure, and it names a subject term
or the page title does. At most 40 per source, then up to 60 more that state
a price (a long comparison page states dozens); a price sentence needs the
page, not the sentence, to name the subject. A sentence under 40 characters
of a longer block is read for a price only ("The Studio plan is $300/year.").

Figures (`parse_metrics`): money with currency (symbols and ISO codes,
before or after the amount, incl. "2,581 USD Million";
thousand/million/billion/trillion, crore, lakh; a currency token right after
a letter is not one, so "HK$" and "S$" are never USD) in a sentence about a
market, industry, spending or revenue -> `market_size` (past or present
year, or the base value after "grow from") or `market_forecast` (future
year, or "expected/projected/reach..."); CAGR with its period; prices (next
section); "N% of <population>" shares. A figure without a parsable year
keeps `year = None` and is never compared.

## Price terms (`market/pricing.py`)

Each amount is read with the words of its own clause (the text between two
amounts is split at its last separator), not the sentence's, so "$99 setup
and $20/month" is a setup fee and a monthly price. Terms recorded are in
`docs/MARKET_DATA_MODEL.md` ("Price terms").

* What it pays for: setup / onboarding / installation words -> setup;
  minimum fee or commitment -> minimum; add-on, extra seats -> add_on; a
  percentage with fee words, a "%" + fixed pair, per transaction / order /
  payment -> transaction; per N calls / GB / messages, overage, pay as you go
  -> usage; per lb / kg / item -> unit; one-time, lifetime -> one_time; a
  period, a seat basis or plan words -> subscription; else unknown (kept, with
  the ambiguity, and never compared). An amount needs a cue to be a price at
  all: a unit after it, price, fee or plan words, a setup/add-on/one-time
  word, or its place (a pricing card, a table cell, a plan's heading).
* Not prices: savings, discounts ("$10 off"), refunds, credits and
  allowances ("$200 in credits", "$5 of mobile messages"), gift cards,
  spend thresholds ("spend at least $500", "orders over $60", "spending under
  $30/month"), revenue, funding, valuation, salaries, fines, investments and
  subsidies (also when a few words come between: "subsidies ... amount to
  between EUR 7,000 and EUR 9,000", unless a price word does), average deal
  or order values, amounts with million/billion (market figures).
* Units: "/user/month", "per 1,000 API calls" (quantity 1000), "per screen
  per month" (any plain word after "per" is a basis); "monthly"/"annually"
  after the amount is the period, "billed annually" the billing; a range or a
  list ("$10-$50 per month", "$0, $17, $47 or $97 a month") and a fee pair
  ("2.9% + 30¢ per transaction") share the unit written once after them.
* Currency: prefix and suffix symbols and codes, "$25CAD" (the code wins
  over the "$"), cents. A bare "$" is USD with `currency_assumed` and an
  ambiguity, or null on a page whose TLD or title names a place with
  another dollar (.sg, .hk, .au, .ca, .nz, ...; Singapore, Canada, ...).
* Bounds ("starts at", "up to", "about"), promotions (first N months,
  introductory, limited time, "now only"), tax included/excluded, included
  allowances, "prices as of / valid until" dates.
* Plan and product: "X plan", a clause's lead label ("Pro: $29", "Brevo
  $8/mo"), "(Starter)" after the amount, the heading above a card or a
  paragraph, a table's column or row. On the vendor's own page or a pricing
  page these are plans; on any other page (a comparison, a review, a blog,
  or a "pricing" page whose title or path says compare / vs / best /
  review) a lead label or possessive ("Kit's 3.5%") is the product, a
  comparison table's row label the product and its column the plan. Under
  an "Add-ons" heading the item's label is the add-on's name.
* Attribution (`extractor market-rules-5`, `Price.attribution`): the page's
  own brand (vendor, `og:site_name`, domain label) is stripped from a plan
  ("Staffly Basic" -> Basic) and is never a product; on third-party pages a
  name ending in a tier word, or a possessive, splits into product and plan
  ("PingWatch Pro", "Billtastic's Growth plan"); a clause's subject names an
  item ("Vine tomatoes - $3.99/kg", "A web licence is EUR 49 per year");
  goods, metered and one-off prices file a heading or label as the product,
  not a plan; an add-on under its own heading takes the heading as its name
  and the plan its clause names ("... on top of your Business plan"); later
  amounts of a sentence whose clauses name nothing keep the plan the sentence
  named ("The Team plan is $12 ..., EUR 11 ..., or GBP 100 per year");
  volume tiers ("1 - 1,000") are never plans; a pricing page's h1 names the
  page, never a price (a shop page's h1 is its product); "last updated" dates
  a vendor's own pricing page only, articles need "prices as of". A plan
  price on a page that names several plans, which nothing ties to one, is
  `uncertain`: kept, no plan guessed, certainty at most 0.6. A publisher's
  price for its own research report is a one-time price of the product
  "research report" and never compared. Styling alone (a CSS class) is not
  read: an add-on with no heading or wording saying so stays a subscription
  (seen live: FreshBooks).
* Text: market pages are parsed with `ParseSettings.inline_spacing`
  (`parse/dom.py`): an inline element boundary becomes a space only where the
  two sides read as separate words (lower-to-upper case, letter and digit, a
  word and a currency symbol, sentence punctuation before a capital), never
  inside an amount, a unit or an identifier ("$" + "29", "4" + ".60", "1" +
  ",000", "example" + ".com"); `sup`/`sub` attach; a block inside an inline
  element is its own line. Default off, so the crawler's text and stored
  evidence offsets (`PARSER_VERSION` 3) are unchanged; each market source
  records `text_version` and is verified in its own mode.
* Regions (`html-text-3`, `ParseSettings.landmark_regions`). Inside the main
  landmark (`<main>`, `role=main`), only landmark elements and roles make a
  boilerplate region (`<aside>`, `<nav>`, an explicit role). A `<header>` or
  `<footer>` there heads a section, and a class or id token is not a
  landmark. Three cases were seen on unseen pages, where the whole article
  became `aside` and its prices were skipped:
  * a WordPress wrapper `cs-sidebar-enabled`;
  * HubSpot `hs_cos_wrapper_widget` modules;
  * a section `<header>` holding a plan card.

  Outside `<main>`, the class hints apply as before. Default off for the
  crawler.
* Recall (`market-rules-6`):
  * **Table cells:** a cell's text may be in elements inside it
    (`<td><p>$2,880</p></td>`).
  * **Headings:** a heading is read for a price it states (a card's "₹9,999/yr"
    in an `h3`, an FAQ question).
  * **Amount forms:** pence ("20p"), paise ("24.5 paisa") and Indian digit
    grouping ("1,54,000").
  * **Price cues** with no price word, each tested against the non-prices next
    to it:
    * "from" or "starts at" directly before the amount;
    * a was-price pair ("£29.50 was £36.00");
    * fares ("one-way", "return", "pp");
    * "an extra $10" (add-on);
    * "supplement";
    * a "group licence";
    * tax wording right after the amount ("+ GST", "excl. VAT");
    * a pack of metered units ("1250 INR + GST for 5,000 SMS Units",
      "1,000 Messages – 295 INR"), read as usage with basis and quantity,
      but not when a plan word comes before it or a period follows, which
      makes it an allowance.
  * **Non-prices:** salaries, wages, earnings and income are not prices.
* Outside sentences (`extract._price_evidence`, at most 60 per page): pricing
  cards (an element under 40 characters stating an amount, with up to three
  following elements stating its unit or billing: "$29" + "per user /
  month" + "billed annually"), table cells read with their row and column
  labels (a "Monthly" column header sets the billing, not a period), and
  JSON-LD `Offer` / `AggregateOffer` (price, lowPrice / highPrice as from /
  up to, `priceCurrency` as stated and never assumed, a priceSpecification's
  period or unit code, the owning product's name). On a pricing page, cards
  in a "header" or "aside" container are read (seen live: Wave); nav and
  footer never are.
* Certainty: a record with an ambiguous price has extraction certainty at
  most 0.6.

Each quote that gave a price a term (the heading, the card's unit element,
the table's row and column labels, the JSON-LD currency) is kept in
`Price.context` with its own locator; the verifier re-resolves each one and
checks that `Metric.text` is in the excerpt (`verify._price_problem`), and the
benchmark's numeric gate re-reads every price's amount and currency with a
parser of its own (`benchmark.reparse_price`: a bare "$" passes only as an
assumed USD or an unsettled currency).

## Market scope (audit M7)

`scope.py` gives every market figure (size, forecast, growth rate) the scope
its own clause states; never the page title's, never another sentence's.

* **geography**: the place the figure's clause names (gazetteer of regions
  and ~40 countries, case-sensitive abbreviations such as "US", "UK", "APAC",
  plus the requested place as written); a place right after the figure ("USD
  3 billion in Europe") wins; several places in one phrase are kept joined
  ("Europe, North America"), never one picked; none = not stated.
* **segment**: customer size, deployment and vertical qualifiers ("CRM market
  for SMBs", "cloud CRM", "the healthcare segment", "..., while SMEs are
  expected to grow"), canonicalized (`SMB`, `Cloud`, ...); words of the
  subject itself are not segments.
* **measure**: `market`, `spending`, `investment` or `revenue` (a revenue that
  names no market: "Salesforce revenue was"; "the market generated a revenue
  of" and "CRM revenue" are the market's).
* **market phrase** (as written), **off_subject** (its head names none of the
  subject's terms: "marketing automation market" in a CRM run; "we estimate
  that the market" is not), **qualifier** (head words beyond the subject's
  vocabulary and generic words: "India CRM marketing services market" ->
  "marketing"), **subset** ("of which USD 30 billion came from new
  customers": a part without its own scope).
* **basis**: `stated`, `carried` (a clause naming nothing continues the
  previous figure of the same sentence: "valued at A in 2023 and expected to
  reach B by 2030"), or `not_stated`.

Metric summaries and contradictions group by kind, unit, year, geography,
segment, measure, subset, other-market and qualifier: a global total and a
regional figure, or two market definitions, are never one range and never a
contradiction. Company revenue is not a market metric (its evidence is kept).
Unresolved scope lowers confidence: one source stating several values for
one scope (`ambiguous`), a part, another market or a qualified market ->
LOW; a geography not stated -> at most MEDIUM. Independent sources of the
same scope still corroborate (HIGH possible) or contradict each other.
Sizing uses only figures of its target scope (`docs/MARKET_SIZING_METHODOLOGY.md`)
and lists every other figure in `sizing.excluded` with the reason.

**Extraction certainty** (on each record): 0.8 when a figure was parsed;
0.6 when the sentence names a subject term; 0.4 when only the page title
does; OCR evidence x 0.6. It says how sure the rules are about filing, not
whether the source is right.

## Source selection and budgets

`market/select.py`, `engine._collect`. Search ranking, source selection,
extraction success and evidence quality are kept apart: the search
position is one input to selection; selection is decided before a page is
fetched, from what is known then (search title, publication date, URL, the
queries and plan questions that found it, publisher class, type); what
extraction yields and the evidence's quality are judged after collection.
No page's contents are assumed before it is read.

**Value of a candidate** = `need_fit x quality`, recorded on every source
as `SourceRecord.selection` (value, need fit, quality and its components,
the questions it targets, the reason, the wave it was fetched in):

* `need_fit`: the open question the candidate most plausibly answers, plus
  0.05 of each other one. A question's weight is how open it is, by the
  independent domains with evidence for it (none 1.0, one 0.5, two 0.2,
  three or more 0); a requested evidence-derived sizing without an eligible
  total yet adds 1.0 to the size question. A question matches 1.0 when one
  of the candidate's queries was written for it and its title shows that
  question's cues ("market size", "pricing", "alternatives", "regulation"...),
  0.6 by the query alone, 0.5 by the title alone. Capped at 2.
* `quality` (0-1): title and URL relevance to the subject (0.35; a title
  naming none of the subject's terms divides quality by 3), requested scope
  named or another place named (0.15), publisher tier (0.15; 1.0/0.85/0.75/
  0.6, no longer a rank penalty), recency of the date or year in title/URL
  (0.10), search position and agreement across queries (0.15), cost (0.10:
  PDF 0.7; x0.3 after a failure on the same domain).

**Waves**: up to 4 candidates, picked greedily (each pick halves the need
of the questions it targets, so a wave spreads over them), fetched
together, then processed in pick order (duplicates, relevance, extraction),
then needs are updated and the next wave picked. Deterministic for the same
fetch results. Collection stops when a budget is used, the deadline passes,
the run is cancelled, or the best remaining value is under 0.08 (every
question corroborated by three domains and the total found: little left to
learn). Candidates never collected are SKIPPED with their last value and
the reason (`source budget: ...`, `over the PDF allowance`, `remaining
candidates add little ...`).

**Budgets** (`Budget`): `max_sources` = search-result sources that gave
evidence (failed, blocked, unreadable, irrelevant, duplicate and empty ones
do not use it, so an alternative is tried); `max_attempts` (default 2 x
`max_sources`) = pages and PDFs requested; `max_html_pages` (default the
attempt budget); `max_pdfs` (default 2; quick 1, deep 5) = PDFs downloaded,
recognised by the URL or by the body's signature (a PDF behind an HTML-
looking URL is not read once the allowance is used); `max_pdf_pages` (50)
per document and `max_extraction_pages` (150) per run, handed out in pick
order so a wave cannot overrun them; `max_total_bytes` (100 MB; checked
between waves, so a wave may overshoot by up to 4 x the download cap);
`max_pages_per_domain` (3). robots.txt refusals are not attempts.
`stats.selection` has the method, the stop reason, what was used and the
limits.

**Coverage**: `stats.questions` says per plan question whether it was
answered, by how many domains and records, how many sources were
collected or left for it, and why it is still open ("3 source(s) collected
for it gave none (2 failed, 1 irrelevant)", "2 candidate(s) not collected:
over the PDF allowance", "its searches were not sent (budget.max_queries or
deadline)"). The same reason follows each "unanswered:" gap. A stop at a
budget with untried candidates for open questions is a warning, so the run
is PARTIAL.

## PDF evidence

Text PDFs (official statistics, research reports) are evidence like pages.
Install: `pip install -e ".[pdf]"` (pdfplumber 0.11, which brings
pdfminer.six). Without it, `.pdf` URLs are SKIPPED ("PDF not read: PDF
support not installed") and a PDF found under another URL is `UNREADABLE`
(`pdf_unavailable`); nothing else changes.

**Acquisition and limits** (`collect.Collector._pdf`, `pdf.py`). Detection:
the `%PDF-` signature in the first 1024 bytes; the declared type may be
`application/pdf`, `application/x-pdf` or `(application|binary)/octet-stream`
(a `.pdf` URL serving HTML is read as HTML). Download: the guarded fetcher
(network guard on every redirect hop, robots.txt, throttle, `fetch`
timeouts, at most 2 retries), body cap `max(budget.max_content_bytes,
market.pdf_max_bytes)` (HTML still held to `max_content_bytes`).
Extraction: a child process (`python -m gtm_crawler.market.pdf`, file on
stdin, JSON on stdout) killed after `market.pdf_timeout` (60 s); on POSIX
also `RLIMIT_AS` = `market.pdf_memory_mb` (1024) and a CPU-seconds limit;
at most `pdf_max_pages` (300) pages and `pdf_max_chars` (3 M) characters
are read (the rest is said). Nothing in a file is executed or followed
(no JavaScript, attachments, links or form actions: pdfminer only reads
text and drawing operators). Refusals are `UNREADABLE` with
`pdf_encrypted`, `pdf_malformed`, `pdf_not_pdf`, `pdf_too_large`,
`pdf_timeout`, `pdf_failed` (the child died), `pdf_no_text` (no page has a
text layer) or `pdf_disabled`; one failing source never ends a run.

**Page targeting** (`pdf.select_pages`, `engine.pdf_focus`). A document longer
than its page grant (`max_pdf_pages`, within the run's `max_extraction_pages`)
is not read from page 1 to the grant. The focus is the rules (dimension
regexes) of the plan questions still open when its wave starts, the subject's
terms and the place. Read: the first 2 pages (title, contents, summary); then,
round after round, the best unread page for each open question in turn, until
the grant is used. A page's score for a question is its rule matches in a
light text pass (`page.extract_text`, no layout or table analysis, at most
500 pages, stopped at 40% of the extraction's time limit so the chosen pages
keep their time), plus a bonus when the page lies in a section whose title
matches the question: sections come from the outline (bookmarks) or, without
one, from the contents lines of the first pages ("Business models ........
40"; the printed-page offset of unnumbered front matter is found by looking
for the entry's title on pages p to p+8), each at most a third of the grant.
Pages off the subject score 0.3x; pages the text pass did not reach in time
score by their section alone. Market-size figure patterns count 3x for the
market-size question (a figure page beats a page that only says "market").
Round-robin keeps one question with many matching pages from taking the
whole grant (a real 59-page report: the outline-only first version read 7
pages for 3 questions and missed the market-size figure). A match only ranks
a page: whether it states a usable figure is still the extraction rules'
decision. Unread pages are stored as empty entries
(`read: false`) so page N stays the file's page N; the extraction records
`targeting` (method, pages, a reason per page) and `pages_read` is what the
page budgets count. A re-extraction for verification reads exactly the
stored pages (`method: explicit`); each page's text does not depend on which
others were read, so `verified_reextracted` holds as before.
`PDF_TEXT_VERSION` is unchanged (page texts are identical), and documents
within their grant, or read without a focus (competitor pages), are read
exactly as before.

**What is kept per page** (one-based): a normalized page text built from
blocks in reading order (headings and paragraphs: lines grouped by spacing
and font size, joined by single spaces; columns read one after the other;
ruled tables: rows on lines, cells joined by ` | `), blocks separated by a
blank line, each with its offsets; ruled tables with rows, cells, the
caption line above and its stated unit ("EUR billion"); page issues
(image-only page: no text layer; irregular table: not used for figures).
Per document: page count, metadata title, author, creation and
modification dates, library versions and `PDF_TEXT_VERSION` (`pdf-text-1`).
The file (`SourceRecord.raw_sha256`) and this extraction
(`extracted_sha256`, which records the file's hash) are both stored.
`published_at` is the metadata creation date (a limitation says so): the
publication date, not the observation period, which stays on each figure's
`year`.

**Evidence**: paragraphs go through the same rules as HTML text; table rows
are evidence with their exact row text. A row gives figures only when its
table states a currency unit, has year columns and a caption that names a
market; each numeric cell under a year is a figure for that year, scoped by
the row label (a place; "Total" = the caption's place; a known segment;
otherwise the label is a qualifier, so the row is never a whole-market
total). M7 scope rules apply unchanged. A PDF figure is one source's
statement: being well extracted does not make it verified (sizing still
needs corroboration).

**Locator** (`Locator`): `type="pdf"`, `value="page=3"` or
`"page=2;table=0;row=1"`, `page`, `char_start`/`char_end` into that page's
normalized text, `version` (`pdf-text-1`), and `table`/`row` (zero-based)
for table rows. Reports cite pages ("[2, p. 3]"); `/ask` returns `page`.

**Verification** (`verify.verify_evidence`, `verify.verify_report`):
checked by the artifacts' hashes, never by URL.
`verified_reextracted` = the stored file re-extracted with the same
version and libraries gives the quote at its page and offsets (HTML: the
stored page re-parsed); `verified_stored` = the quote is at its location in
the stored extraction, but the file was not re-extracted or the
re-extraction is not comparable (another version or library version, said
in the detail); `mismatch` = not at the recorded page/offsets/row (the
detail says where it is, if anywhere: another page is still a mismatch),
or the stored text disagrees with a re-extraction of the same file;
`not_verifiable` = artifacts missing or damaged, an extraction of another
file, a locator of another extraction version, or a locator kind not
checked here; `source_changed` = the URL now serves another file (hash),
with the check against the stored copy in the detail. The benchmark's
provenance gate uses `verify_report`.

**Supported**: text PDFs with a text layer (statistical releases, reports,
brochures), one or more text columns, ruled tables. **Not supported**:
scanned pages (reported as image-only; PDF OCR is not implemented: the
optional OCR covers web images only), encrypted files, tables drawn without
lines (their rows stay as text lines, no figures), merged or empty cells
(flagged irregular), text in charts (read as text, often as label runs),
right-to-left and vertical text (untested), forms and attachments (ignored).
On Windows the child has no memory limit: a decompression bomb is bounded
only by the timeout and the size cap.

**Quality on the labelled fixtures** (`tests/test_market_pdf_quality.py`,
5 hand-made files): text passages on their page 9/9; figures with value,
unit, year and place 8/8 with none extra; page locators 6/6; quotes
verified by re-extraction 7/7; ruled tables reconstructed 1/1; irregular
tables flagged 1/1; image-only pages reported 1/1; unruled tables detected
0/1 (rows kept as text 3/3, no figures 0/4). These describe five simple,
generated files, not real-world PDFs.

## Evidence quality

Kept separate (`assessment.py`):

* **Source quality** (`SourceQuality`), components 0-1 or unknown:
  authority (tier 1 = 1.0, 2 = 0.8, 3 = 0.6, 4 = 0.4; a vendor's own page
  0.5 for market claims), recency (published <= 1 year 1.0, <= 3 0.7, <= 5
  0.4, older 0.2; no date = unknown, noted), methodology (stated: 1.0, not
  stated: 0.3), geographic relevance (requested geography named 1.0, only
  "global" 0.6, neither 0.3; unknown when no geography requested),
  specificity (share of its evidence with figures). `score` = mean of the
  known components: an ordering aid, not a probability.
* **Classification** (`classify`): government suffixes and statistics
  domains -> `government`, tier 1; listed market-research publishers ->
  `market_research`, tier 3 (headline figures from paywalled reports with
  undisclosed methods); then the crawler's own lists (regulatory,
  business database, major publication, industry publication, professional
  profile); else unverified tier 4. All lists are configuration (`[market]`, `[sources]`).
* **Claim confidence** (`Confidence`): HIGH = >= 2 independent domains,
  best source quality >= 0.7, no unresolved contradiction; MEDIUM = best
  quality >= 0.6 or >= 2 domains; LOW = other evidence, any contradiction,
  or only user/inferred basis; INSUFFICIENT = none. Rules, not calibration.
* **Independence** = distinct registrable domains.

## Contradictions

Same metric (kind, unit, year - growth rates by period end - and the full
market scope), at least two domains, values more than 25% apart (growth
rates: more than 3 points); parts of larger figures and other markets are
never compared.
Both (all) values are kept, each record lists the others in
`contradicted_by`, the metric is reported as a range with confidence LOW,
and the explanation lists possible causes (definitions, publication dates,
missing dates, unstated geography). Values from one domain are not compared
(pages break markets into regions); figures without a year are not compared.
No side is preferred.

Geography: a figure whose clause names a place is scoped to it; one that
does not is "geography not stated" (it may be global or a region). They are
different metrics, reported separately, never a contradiction. When a
geography is requested, sizing never uses an unstated one.

## Analysis

Findings are one of: `direct` (a quoted excerpt with its publisher),
`derived` (a figure summarized across sources, observed price ranges),
`user` (the requester's input, confidence LOW), `inferred` (a rule's
hypothesis or recommendation, confidence LOW, phrased as such). Sections
show at most 8 findings, ranked by source quality, certainty and figures,
at most two per domain. A section with no evidence is a gap; unanswered
plan questions are listed in `gaps`.

* Industry sections: definition and scope, size and growth (figure
  summaries first), segments, demand, competitive structure, trends and
  external drivers, barriers and risks.
* Product sections: product and problem definition (user statements; gaps
  for missing problem/target/price), segments, demand validation,
  competitors and substitutes (incl. every excerpt from competitor pages and
  the dossier), pricing (below), category size and
  growth, barriers, differentiation (each proposed differentiator checked
  against competitor excerpts: found = likely parity; not found = absence
  of evidence, not uniqueness), go-to-market (hypotheses from the median
  of the largest set of monthly subscription prices in one set of terms -
  one currency, basis and billing; a self-serve/sales threshold only for
  USD, EUR, GBP - and enterprise gating), validation experiments (prioritized
  where evidence is thin), and the statement that desk research cannot
  establish product-market fit.

Pricing (`analyze.price_comparison`): prices are set side by side only when
their terms are the same (`price_key`: pricing type, currency or percent,
period, billing commitment, basis and its quantity), one `derived` finding
per set of terms, each price named by its vendor or product and plan, with
the range, median, count, how many are starting prices or have the currency
read from a bare symbol, and "different amounts stated for X" when one
vendor or plan has more than one amount in the same terms (another plan,
date or source: the excerpts say which). Left out, each listed in the
section's gaps with why (at most 10, then a count): quote-based (no
amount), no price terms (records from before them), currency not stated or
unclear, what the amount pays for not stated, a subscription with no
period, promotional prices, upper bounds and approximate amounts. No
currency conversion, no monthly/annual conversion: a USD monthly price and
a USD annual price are two findings, and "$25/month billed annually" is not
compared with "$29/month" billed monthly. When nothing is comparable the
gaps say so.

## Market sizing

See `docs/MARKET_SIZING_METHODOLOGY.md`. Deterministic arithmetic only;
the evidence-derived top-down total is marked unverified unless HIGH
confidence and uncontradicted.

## Opportunity scoring

`scoring.py`: 11 dimensions rated 1-5 for favourability (meanings in
`scoring.MEANING`), weights configurable (`ScoringRequest.weights`,
normalized; defaults in `DEFAULT_WEIGHTS`). Ratings: user-given (basis
`user`) override evidence-derived (basis `evidence`): market growth from the
median stated CAGR (<3% 1, <7% 2, <12% 3, <20% 4, else 5); demand, pain and
willingness to pay from the number of independent domains with such
evidence (1: 2, 2: 3, 3-4: 4, 5+: 5, labelled proxies); competitive
intensity and regulatory barriers inversely (1: 4, 2-3: 3, 4+: 2). Score =
weighted mean of rated dimensions minus (1 - coverage) x 1.0, floor 1, one
decimal. `config_id` hashes method and weights: compare only equal ids.
With several candidates, each weight is moved +-25% and any rank change is
reported. Candidates are the subject (all evidence) and each requested
segment (evidence naming it).

## Model use (optional)

Off unless `market.llm_model` is set (OpenAI-compatible endpoint,
`llm_base_url` https, or http with host exactly localhost / 127.0.0.1 / ::1;
key via `GTM__MARKET__LLM_API_KEY`).
Used for the `narrative` section (when `budget.max_llm_calls >= 1`) and
`POST /v1/market/research/{id}/ask` answers. Excerpts go in `<evidence>`
tags with `<`/`>` neutralized and a system instruction that their content
is data; every kept sentence must cite ids from the prompt and state only
numbers that its cited excerpts state; nothing else is
stored. Never used for figures, sizing, scores or confidence. The engine
works fully without it.

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `GTM__SEARCH__API_KEY` | empty | Exa search; without it only known competitor pages, dossier and cached evidence are used (reported as an issue) |
| `identity.user_agent_contact` | required | Same crawler identity for market fetches |
| `[market] government_suffixes`, `statistics_domains`, `market_research_domains` | see config.example.toml | Source classes |
| `[market] max_queued_runs` | 100 | API backpressure (429) |
| `[market] ocr_enabled`, `tesseract_cmd`, `ocr_max_images_per_page`, `ocr_max_image_bytes` | off, "", 4, 5 MB | OCR (`pip install -e .[ocr]` + Tesseract) |
| `[market] llm_base_url`, `llm_model`, `llm_api_key`, `llm_timeout`, `llm_max_evidence` | off | Optional model |

## Usage

```bash
alembic upgrade head                          # migration 0016: market_research
gtm-market request.json --markdown            # one run in this process (no DB needed)
gtm-market request.json --save                # ... and store it in market_research
gtm-market --ad-checklist COMPANY_ID [--country IN] [--markdown]       # ads to capture
gtm-market --import-ads ads.json --operator NAME [--screenshots DIR]   # migration 0019
curl -X POST localhost:8000/v1/market/research -H 'content-type: application/json' \
     -d '{"mode": "industry", "subject": "CRM software", "geography": "India",
          "sizing": {"market_definition": "CRM software sold in India", "year": 2025}}'
curl localhost:8000/v1/market/research/<id>                    # status and progress
curl localhost:8000/v1/market/research/<id>/report?format=markdown
curl -X POST localhost:8000/v1/market/research/<id>/ask -d '{"question": "..."}' \
     -H 'content-type: application/json'
```

Product request example:

```json
{"mode": "product", "subject": "MyCRM", "industry": "CRM software", "geography": "India",
 "segments": ["small businesses", "real estate agents"],
 "product": {"name": "MyCRM", "problem": "small businesses lose leads tracked in spreadsheets",
             "target_customer": "small businesses", "price": "$15 per user per month",
             "known_competitors": [{"name": "Acme", "url": "https://acme.example"}],
             "differentiators": ["WhatsApp-native inbox"], "company_id": 42},
 "sizing": {"market_definition": "CRM seats for Indian SMBs", "year": 2025, "bottom_up": {
   "customers": {"name": "smbs", "base": 600000, "low": 400000, "high": 800000,
                 "unit": "companies", "basis": "assumption", "rationale": "MSME registry share"},
   "units_per_customer": {"name": "seats", "base": 3, "unit": "seats", "basis": "assumption",
                          "rationale": "median team size"},
   "price_per_unit": {"name": "price", "base": 180, "unit": "USD/seat/year",
                      "basis": "assumption", "rationale": "planned list price"},
   "sam_filters": [{"name": "digital_ready", "base": 0.3, "unit": "fraction",
                    "basis": "assumption", "rationale": "uses email+smartphone", "dimension": "readiness"}],
   "penetration": {"name": "penetration", "base": 0.02, "low": 0.005, "high": 0.05,
                   "unit": "fraction", "basis": "assumption", "rationale": "3-year target"}}}}
```

## Implementation checklist

| # | Task | Status | Verification |
|---|---|---|---|
| 1 | Audit, baseline, assessment doc | DONE | baseline runs above; `ARCHITECTURE_ASSESSMENT.md` |
| 2 | Data model (requests, sources, evidence, metrics, reports, sizing, scoring) | DONE | `test_request_validation`, `test_quantity_validation`, JSON round trip |
| 3 | Plan with query budget | DONE | `test_plan_round_robin_and_budget` |
| 4 | Guarded single-page collection, budgets, cancellation, timeout | DONE | e2e statuses, `test_duration_budget...`, `test_cancellation`, `test_source_budget...` |
| 5 | Rule-based extraction with re-checkable quotes | DONE | `test_evidence_quotes_resolve...`, raw-store re-check in e2e |
| 6 | Source quality, confidence, contradictions | DONE | `test_contradictions_*`, `test_confidence_rules` |
| 7 | Industry vertical slice | DONE | `test_industry_run_end_to_end` |
| 8 | Product slice incl. dossier reuse, differentiation, GTM, experiments | DONE | `test_product_run_with_competitors_and_dossier` |
| 9 | Sizing (top-down, bottom-up, scenarios, sensitivity, safeguards) | DONE | 5 sizing tests |
| 10 | Scoring (weights, coverage penalty, user override, sensitivity) | DONE | 2 scoring tests |
| 11 | Cache fallback when search is down | DONE | `test_cached_evidence_is_reused...`, worker test |
| 12 | Store, worker, API, CLI | DONE | `tests/test_market_service.py` (PostgreSQL), CLI test |
| 13 | OCR (from competitor_analy2) | DONE, not live-tested | fake reader test; Tesseract not installed here |
| 14 | Retrieval Q&A and optional model (from competitor_analy2) | DONE, not live-tested against a real model | mocked endpoint tests incl. citation filtering and injection containment |
| 15 | Docs, config example, README | DONE | this file |
| 16 | Live runs against real search and sites | DONE (2 small runs) | see "Live verification"; 5 defects fixed with regression tests |
| 18 | Market scope per figure (M7), coverage-based status, cache key, competitor budget and rendering | DONE | `test_scope_*`, `test_live_*`, `test_completed_only_when_every_research_question_has_evidence`, `test_cache_key_separates_products_of_the_same_name`, `test_competitor_pages_have_a_budget`, `test_js_rendered_competitor_pricing_is_rendered` (real Chromium) |
| 21 | Question-driven source selection and explicit budgets | DONE | `tests/test_market_selection.py` (15: the 8 benchmark scenarios, value stop, 5 review regressions, unsent-search reason); live run 7 and old-vs-new fixture comparison in `docs/MARKET_BENCHMARK.md` |
| 20 | PDF evidence: detection, isolated extraction, page locators, verification | DONE (text PDFs; no PDF OCR) | `tests/test_market_pdf.py` (33 tests), `tests/test_market_pdf_quality.py` (1); live runs 4-6 in `docs/MARKET_BENCHMARK.md`; 3 defects found live and fixed |
| 19 | Live benchmark and quality gates | DONE | `docs/MARKET_BENCHMARK.md`, `tests/test_market_benchmark.py`; 3 live runs + API/worker run, all gates pass |
| 22 | Price terms: model, extraction in context (sentences, cards, tables, JSON-LD), comparison in compatible terms, price checks in the verifier and numeric gate | DONE | `tests/test_market_pricing.py` (42: the milestone's 14 examples and live regressions, cards/tables/JSON-LD, comparison and exclusions, verifier and gate, a floor under the fixture benchmark); pricing benchmark (175 fixture cases, 448 live items, blind labels) with old-vs-new scores in `docs/MARKET_BENCHMARK.md` |
| 23 | Plan/product attribution: inline-boundary spacing (versioned, offsets kept), DOM-based plan and product, add-ons vs base plans, uncertain attribution, page-level headings and dates not attached to every price, record-level scoring | DONE | `tests/test_parse_spacing.py` (20), `tests/test_market_attribution.py` (15); fixture product+plan 54.5% -> 78.2%, full record 49.5% -> 69.2% (`docs/MARKET_BENCHMARK.md`, "Price attribution") |
| 24 | Held-out pricing set: frozen snapshots and labels, two blind labellers, adjudication, human-review sample, scored once | DONE (human review pending) | `benchmarks/market/pricing/heldout/` (README, manifest, labels, adjudication, results); `docs/MARKET_BENCHMARK.md`, "Held-out pricing set" |
| 25 | Long-PDF page targeting: first pages, outline/contents sections, time-bounded text pass, round-robin per open question; locators, hashes and quote verification unchanged | DONE | `tests/test_market_pdf_targeting.py` (12, incl. pages 54-55 reached in a research run with verified quotes, blocked PDF + alternative, page-budget exhaustion); real 59-page report check in `docs/MARKET_BENCHMARK.md`, "Long PDFs" |
| 26 | Price recall on unseen layouts (landmark regions, cells with inner elements, price headings, pence/paise/Indian grouping, cue words), measured on a fresh held-out set | DONE; small held-out gain | `tests/test_market_recall.py` (5); recall on heldout-v1 (development) 49.2% -> 83.6%, but on heldout-v2 (held out, scored once) only 52.3% -> 54.2% at 82.7% precision (`docs/MARKET_BENCHMARK.md`, "Recall work and heldout-v2") |
| 17 | Post-implementation audit | DONE | `docs/POST_IMPLEMENTATION_AUDIT.md`: 3 High and 7 Medium fixed, 1 Medium open; 3 live runs; workflow tests `test_industry_report_integrity`, `test_product_report_separates_facts_interpretations_and_unknowns`, `test_independent_check_of_bottom_up_scenarios_and_sensitivity`, `test_worker_runs_a_waiting_crawl_job_before_a_market_run` |

## Live verification (2026-10-21)

Two small live runs with Exa and real public sites (`gtm-market`, quick
budgets); results in the session's scratch files, not committed.

| Run | Before fixes | After fixes |
|---|---|---|
| Industry "CRM software", India; 4 queries, 8 sources | 0 evidence: the tier-first order put official PDFs and 403 pages first and they used up the budget | 7 pages fetched, 159 excerpts, India and unscoped figures reported separately, sizing from the India figure (unverified) |
| Product "LeadBook" (CRM for small businesses), Zoho as known competitor; 3 queries, 5 sources | - | 7 sources, 120 excerpts; a search-found vendor page already describing a "differentiator" is now flagged |

Defects found live and fixed, each with a regression test: budget used up
by failed/PDF sources (`test_failed_sources_do_not_use_up_the_budget`);
tier deciding order alone (`test_tier_raises_priority_without_deciding_alone`);
India vs unstated-geography figures reported as a contradiction
(`test_scoped_and_unscoped_figures_are_different_metrics`); differentiators
checked only against known competitors
(`test_differentiators_found_outside_competitor_pages_are_flagged`); "includes"
pulling pricing sentences into the definition section and heading fragments
counted as statements (rules tightened); `gtm-market` crashing on a Windows
console that cannot print "₹" (output is now written as UTF-8).

Audit runs (2026-10-21, after the audit fixes; same budgets class): industry
"CRM software", India (3 queries, 5 sources): PARTIAL, 4 pages fetched, 2
answered 403, 9 PDFs skipped, 138 excerpts; product "LeadBook" with Zoho CRM
as known competitor (3 queries, 4 sources): PARTIAL, 7 fetched, 3 x 403, 104
excerpts. In both, every excerpt re-derived from its stored raw page and no
finding or metric cited a missing evidence id; the evidence-derived TAM was
marked not verified. The product run exposed "2581.22 USD Million" not being
parsed and a "grow from" base value filed as a forecast; both fixed
(`test_amount_currency_scale_order_is_parsed`).

Seen live and not fixed: many official and analyst pages answer 403 to
crawlers or are PDFs; some excerpts are off-scope (a survey of another
country's SMBs). The benchmark (`docs/MARKET_BENCHMARK.md`, 2026-10-21) is
the current live baseline: 5 cases, 3 runs plus one through the API and a
worker.

## Known limitations

* Rules miss and misfile: sentences without the keywords are not
  evidence; a sentence can land in a section by a broad keyword (e.g.
  "enterprise" -> segment). Market figures in HTML tables are not parsed
  (text blocks only; HTML table cells are read for prices); PDF tables only when ruled and stating a unit (see "PDF
  evidence" for what PDFs cannot do).
* Selection judges a page by its search title, URL and date only: a
  relevant title over an empty page (a landing page, a 403-prone publisher)
  still costs an attempt. Long PDFs are targeted by rule matches (see "Page
  targeting"): a page that states a figure in words no rule knows is not
  ranked, and an image-only page has no text to rank. A source whose host is
  unreachable is `BLOCKED` (robots.txt unreachable is read as disallow-all)
  and the run moves on to other candidates; with all alternatives blocked,
  over a per-domain budget or below the value floor, the question stays open
  (the plant-based case, `docs/MARKET_BENCHMARK.md`).
* Evidence per source is capped (40 excerpts): on a long PDF read with a
  large grant the cap, not the page choice, limits what is kept.
* Market-research publisher pages usually show teaser figures for paywalled
  reports; they are classified tier 3 and conflicts with official
  statistics are surfaced, not resolved.
* Currency conversion is never done: figures in other currencies are kept
  but not compared or used as a top-down total. Prices likewise: no
  conversion, no monthly/annual conversion; prices in other terms are listed
  apart, not converted to be comparable.
* Prices (`docs/MARKET_BENCHMARK.md`, "Pricing"): plan and product names are
  still the weakest terms (see the held-out results there). Add-ons marked
  only by styling (CSS classes, no heading or words: FreshBooks) are filed as
  subscriptions of the nearest plan. Inline spacing splits only at visible
  boundaries (lower->Upper, letter/digit, before a currency symbol, after
  punctuation): two lowercase-led inline words ("Starter" + "billed") still
  join. A price with several plans on the page and no plan of its own is
  `attribution: uncertain`, not guessed. Amounts with no currency
  ("29/month") are not prices to the rules. A market-research page's
  report licence is `one_time`, product "research report", and excluded
  from comparisons (`report_price`). Dossier price facts reach the report as dossier
  evidence without price terms (excluded from comparisons with that reason).
* Several price behaviours differ from the held-out labels on purpose. Each
  shows up as a held-out error:
  * Identical text repeated on a page (monthly/annual tab panels, a mobile
    copy) is one evidence record; the labels count every occurrence.
  * A bare "$" on a page whose domain or locale is another dollar's (.au,
    .nz, .ca, .sg, .hk) keeps its currency unsettled rather than assumed. The
    numeric gate accepts a bare "$" only as assumed USD or unsettled.
  * "¥" stays unclear (yen or yuan).
  * On a vendor's table, a row name (a course, a service) is read as the
    plan; the labels call services products and tiers plans.
* Competitor discovery inside market runs is limited to search results and
  given competitors; resolved competitor crawls need the crawler
  (`POST /v1/crawls`, then `company_id`).
* A worker runs one thing at a time and prefers crawl jobs: a market run in
  progress delays the next crawl job by up to its `max_duration_seconds`
  (default 10 minutes). Run separate workers if that matters.
* Opportunity scores from evidence volume are proxies; they measure how
  much is written, not how much demand exists.
* Scope is read by rules: sub-markets outside the segment lexicon (service
  types, applications: "CRM Migration and Modernization ... 12.74% CAGR") are
  read as the whole market; a forecast year before the figure ("from 2026 to
  2032, reaching USD 4.12 Bn") is not attached; a place the gazetteer does
  not know and was not requested (a state, a city) is "not stated", so with
  no geography requested such a figure can be used as a (flagged,
  unverified) Global fallback. The benchmark's labelled accuracy is 49/58
  figures fully correct (`docs/MARKET_BENCHMARK.md`).
* Cache keys changed (industry and product hash): runs stored before are not
  reused as a cache.
* Only competitor pages are rendered in a browser (script shells, pricing
  pages stating no price; at most 8 per run); search-found pages are not.
  OCR image downloads are outside the per-host throttle slot (off by
  default, 4 images per page).
* OCR and the optional model have never run against real Tesseract or a
  real model endpoint.

## Next steps

1. Prices, first: locale number formats and currencies. Decimal commas and
   dot thousands ("R$ 29,90", "1.299,00") are misread today. kr, zł, the
   rand "R" and COP are not read at all. The numeric gate misses
   "1.299,00", because its own re-read makes the same mistake. These pages
   account for the 6 heldout-v2 pages missed entirely
   (`docs/MARKET_BENCHMARK.md`, "Recall work and heldout-v2"). After that:
   get the held-out samples human-reviewed
   (`heldout/human_review_sample.json`). Then work on the held-out errors, and
   build `heldout-v2` before quoting a new held-out score, because v1 counts
   as development data once its errors guide changes. Market figures: unruled
   PDF tables and HTML tables. PDF evidence: raise or split the per-source
   excerpt cap for long documents.
2. Grow the labelled benchmark set (more cases and industries) so figure
   accuracy can be quoted as a rate, and extend the segment lexicon to
   service types and applications.
3. Run a real model endpoint once against the narrative/`/ask` validation.
