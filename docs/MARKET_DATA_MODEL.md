# Market research data model

Source of truth: `gtm_crawler/market/models.py` (pydantic, `extra="forbid"`:
unknown fields are errors). `SCHEMA_VERSION` (on every report) is bumped when
a stored report's shape changes (2: market scope on metrics and summaries, sizing
exclusions); `analysis_version` names the analysis rules.

## Request

`ResearchRequest`

| Field | Type | Notes |
|---|---|---|
| `research_id` | str | `mr_<12 hex>`; assigned by the server for API requests |
| `mode` | `industry` \| `product` | product requires `product` |
| `subject` | 1-200 chars | the industry, or the product's name |
| `description`, `geography`, `segment`, `industry` | optional text | `industry` is the product's category in product mode |
| `segments` | up to 10 | candidates for opportunity scoring |
| `time_horizon_years` | 1-30 | default 5 |
| `currency` | ISO 4217 code | default USD; no conversion is ever made |
| `assumptions` | up to 50 texts | reported verbatim as assumptions |
| `depth` | quick \| standard \| deep | sets the budget when `budget` is absent |
| `budget` | `Budget` | hard limits, each with a validated maximum: queries, results per query, useful sources (`max_sources`), attempts, HTML pages, PDFs, PDF pages per document and per run, bytes, pages per domain, timeouts, duration, model calls (docs/MARKET_ENGINE_IMPLEMENTATION.md, "Source selection and budgets") |
| `product` | `ProductInput` | name; optional description, problem, target customer, known competitors (name, url), business model, price, stage, proposed differentiators, `company_id` of a crawled company |
| `sizing` | `SizingRequest` | see the sizing methodology |
| `scoring` | `ScoringRequest` | weights and user ratings |

`subject_key()` = mode, subject, geography and segment, lower-cased: two
runs with the same key study the same thing (cache fallback).

## Sources

`SourceRecord`: `source_id`, `url`, `final_url` (after redirects), `domain`
(registrable), `publisher` (og:site_name / application-name / author),
`source_type` (government, regulatory_filing, business_database,
major_publication, industry_publication, market_research, company_website,
professional_profile, crawled_dossier, unverified_third_party), `tier` 1-4,
`title`, `published_at`, `retrieved_at`, `content_sha256` (main text),
`raw_sha256` (raw store key), `extracted_sha256` (a PDF's stored extraction; for a
document longer than its page budget it records `targeting`: method `outline + text` /
`contents + text` / `text` / `explicit`, `pages`, `scanned` (pages the light text
pass reached in time) and `reasons` (`[page, why]` pairs); unread pages are empty entries
with `read: false`, so page N is always the file's page N), `status` (fetched, cached, blocked, failed, unreadable,
irrelevant, duplicate, skipped) with `status_detail`, `discovered_by` (the
query, `known_competitor:<name>`, or `dossier`), `extraction_method` and
`extraction_version`, `text_version` (`html-text-2`: the page's text was built with
inline element boundaries spaced, `ParseSettings.inline_spacing`; `html-text-3`: also
landmark regions, `ParseSettings.landmark_regions`, so class names inside `<main>` do not
make content boilerplate; null: the parser's
legacy text, for sources collected before; quotes are re-checked against the stored page
parsed the same way), `quality` (`SourceQuality`), `limitations`, `size_bytes`,
`search_title`, `selection` (`SourceSelection`: value, need fit, quality and
its components, targeted plan questions, reason, wave).

Every source tried is recorded, including blocked, failed, duplicate,
irrelevant and over-budget ones: coverage is auditable.

## Evidence

`EvidenceRecord`: `evidence_id`, `source_id`, `claim`, `excerpt` (verbatim),
`locator` (`xpath` + char offsets, re-derivable with `evidence.resolve`
from the raw page; `pdf` = `page` (one-based) + char offsets into that
page's normalized text + `version`, and `table`/`row` for table rows,
re-checked with `verify.verify_evidence`; `dossier` = the crawler's locator; `image` = OCR'd image
URL), `dimensions` (at least one), `kind` (direct, derived, inferred, user),
`metrics` (`Metric`: kind market_size / market_forecast / growth_rate /
price_point / price_on_request / share, value, unit, year or period, the
matched text; for market figures the scope the sentence states: `geography`,
`segment`, `measure`, `market_phrase`, `off_subject`, `qualifier`, `subset`,
`scope_basis`; for prices `price`, below),
`period`, `geography` (when the excerpt names the requested one), `segment`,
`extraction_certainty` 0-1, `contradicted_by` (ids), `cached_from` (the run
it was collected in, when reused).

### Price terms

A price is a `Metric` (kind `price_point`, or `price_on_request` for "contact
sales" with value 0 and unit `none`) with `price` (`Price`): the terms as the
source states them. Nothing is converted or inferred: no currency
conversion, no period when none is written, no monthly/annual conversion.
The evidence record carries the rest: `excerpt` (the quote, which contains
the amount; `Metric.text` is the amount with its unit as written), `locator`,
`source_id` (and through it the source's retrieval date).

| Field | Type | Meaning |
|---|---|---|
| `pricing_type` | `PricingType` | subscription, one_time, setup, transaction (fixed or percentage, per payment/order), usage (per N API calls, GB, messages; overage), unit (goods per lb, kg, item), add_on, minimum, quote, unknown (stated as a price; what it pays for is not) |
| `currency` | ISO 4217 or null | as written ("S$" SGD, "HK$" HKD, "₹"/"Rs." INR, a code after the amount); null for a percentage, or for a bare "$" on a page about a place with another dollar |
| `currency_assumed` | bool | read from a bare symbol ("$" as USD) |
| `percent` | bool | the value is a percentage ("2.9% per transaction") |
| `period` | string or null | month, year, week, day, hour, quarter, only when written ("/mo", "per year", "a month") |
| `billed` | string or null | monthly, annually, quarterly: a stated billing commitment ("billed annually", an "Annual" column), kept apart from `period` |
| `basis`, `basis_quantity` | string, number or null | per what besides time: user, seat, transaction, lb, "api call" with quantity 1000 |
| `bound` | exact, from, up_to, about | "starts at", "up to", "about" |
| `plan`, `product`, `vendor` | string or null | the plan or tier (without the page's own brand: "Staffly Basic" on staffly.sg is `Basic`); the product priced when named apart from the vendor (an add-on, a goods item, a metered service, a tool on a comparison page, `research report` for a publisher's own licence price); the known competitor whose own page states it |
| `attribution` | `Attribution` or null | where plan/product were read: `clause` (the price's own words), `heading`, `table`, `structured_data`; `uncertain`: the page names several plans and nothing ties this price to one, so none is guessed (an ambiguity says so; comparisons name it "(plan not identified)"); null: no plan or product named and none needed |
| `promotional` | bool | first months, introductory, limited time, "now only" |
| `tax` | included, excluded or null | when stated, on the price or the page |
| `included` | string or null | a stated allowance ("500 contacts") |
| `effective` | string or null | a "prices as of / valid until / updated" date the source states |
| `context` | list of `PriceContext` | each quote that gave the price its terms (role heading, card, row, column, json), with its own locator, re-checked like the excerpt |
| `ambiguities` | list of strings | what a reader must know first ("no billing period stated", "currency read from a bare '$' as USD", "a starting price, not every customer's price") |

A price with the ambiguity "the price of a research report itself, not of the market's
products" (a publisher's "Single User License", "the full report costs") is kept as
evidence but never compared (`analyze.not_comparable`).

`Metric.unit` is a readable summary: currency (or `percent`, or `unknown`),
then basis with quantity, then period, e.g. `USD/user/month`,
`percent/transaction`, `USD/1000 api call`, `unknown/month`. Comparisons use
the fields, not the unit string. Sourced, derived and inferred: every price
is sourced (a quote); a comparison across prices is a `derived` finding
listing its prices and their evidence; no price is inferred, and no
conversion is made, so there is no converted value to label.

## Findings and report

`Finding`: `statement`, `evidence_ids`, `confidence` (high, medium, low,
insufficient), `basis` (direct, derived, inferred, user),
`independent_domains`. `Section`: title, findings, gaps.

`Report`: schema and analysis versions, `research_id`, `mode`, `subject`,
`generated_at`, the full `request`, `status` (completed, partial, failed,
cancelled), `executive_summary`, `sections` (keyed: industry `definition`,
`size_growth`, `segments`, `demand`, `competition`, `trends`, `risks`;
product `problem`, `segments`, `demand`, `competitors`, `pricing`,
`size_growth`, `barriers`, `differentiation`, `go_to_market`,
`validation`; optional `narrative`), `metrics` (`MetricSummary` per kind,
unit, year and scope: low, high, evidence, domains, confidence, contradicted,
ambiguous, notes), `sizing` also carries `target_scope` and `excluded`
(figure, evidence ids, reason),
`contradictions`, `sizing`, `scoring`, `assumptions`, `gaps`,
`limitations`, `issues` (stage, severity, message), `sources`, `evidence`,
`stats` (plan, queries sent/failed/not sent, sources by status, `coverage`,
`status_reasons`, `selection` (method, stop reason, budgets used and limits),
`questions` (per plan question: answered, domains, evidence, tried, untried,
reason), duration, model use).

Integrity: every `Finding.evidence_ids` entry is in `evidence`; every
evidence `source_id` is in `sources` (tested end to end).

## Storage

`market_research` (migration 0016): `id` (= research_id), `mode`,
`subject_key`, `status`, `request` (JSONB), `progress` (JSONB, latest stage
reported by the worker), `report` (JSONB, the `Report`), `error_summary`,
lease columns (`lease_owner`, `lease_expires_at`, `attempts`),
`cancel_requested`, timestamps.
